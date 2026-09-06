from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
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
from rallymate_scoring.calibration_handoff import (
    _artifact,
    _canonical_sha256,
    _copy_bound,
    _fit_readiness,
    _read_json,
    _read_jsonl,
    _sha256_file,
    _write_json,
    _write_jsonl,
)
from rallymate_scoring.calibration_measurement_sources import (
    load_latest_calibration_measurement_sources,
    validate_calibration_measurement_sources,
)
from rallymate_scoring.manual_event_features import build_manual_event_features


SCHEMA_VERSION = "1.0.0"
PORTFOLIO_VERSION = "scoring-truth-calibration-portfolio-v1.0.0"
LATEST_VERSION = "scoring-truth-calibration-portfolio-latest-v1.0.0"


def _render_index(manifest: Mapping[str, Any], output_path: Path) -> None:
    rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            item["video_id"],
            item["frame_count"],
            item["manual_event_count"],
            item["manual_feature_count"],
        )
        for item in manifest["video_measurements"]
    )
    fit_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td></tr>".format(
            item["indicator_id"],
            "yes" if item["fit_ready"] else "no",
            item["fit_blocker"] or "external fit protocol required",
        )
        for item in manifest["indicator_readiness"]
    )
    artifacts = manifest["artifacts"]
    refresh_href = os.path.relpath(
        artifacts["truth_refresh_manifest"]["path"], output_path.parent
    ).replace("\\", "/")
    source_href = os.path.relpath(
        artifacts["measurement_source_manifest"]["path"], output_path.parent
    ).replace("\\", "/")
    readiness_href = os.path.relpath(
        artifacts["calibration_dataset"]["readiness"]["path"], output_path.parent
    ).replace("\\", "/")
    html = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<title>RallyMate M49 三视频标定交接</title><style>body{{font:15px/1.55 system-ui;margin:28px;max-width:1180px}}table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #bbb;padding:6px;text-align:left}}code{{word-break:break-all}}.warn{{background:#fff4d6;padding:12px}}</style></head><body>
<h1>M49：三视频真值到标定组合</h1><p class=\"warn\">状态 <code>{manifest['status']}</code>。这是 manual-boundary 特征与 prepared dataset 的可追溯交接，不是 A～E 评分。</p>
<p><a href=\"{refresh_href}\">M47 truth refresh</a> · <a href=\"{source_href}\">三视频 measurement sources</a> · <a href=\"{readiness_href}\">calibration readiness</a></p>
<p>覆盖 {manifest['counts']['measurement_source_videos']}/{manifest['counts']['truth_manifest_videos']} 个视频、{manifest['counts']['measurement_source_frames']} 帧；人工事件/特征/sample 为 {manifest['counts']['accepted_manual_events']}/{manifest['counts']['manual_feature_records']}/{manifest['counts']['calibration_samples']}，fit-ready {manifest['counts']['fit_ready_indicators']}/{manifest['counts']['indicators']}。</p>
<h2>视频测量源</h2><table><thead><tr><th>video</th><th>frames</th><th>manual events</th><th>manual features</th></tr></thead><tbody>{rows}</tbody></table>
<h2>逐指标拟合前门禁</h2><table><thead><tr><th>indicator</th><th>fit-ready</th><th>blocker</th></tr></thead><tbody>{fit_rows}</tbody></table>
</body></html>"""
    output_path.write_text(html, encoding="utf-8")


def build_scoring_truth_calibration_portfolio(
    *,
    truth_refresh_latest_path: str | Path,
    measurement_sources_latest_path: str | Path,
    output_root: str | Path,
    portfolio_id: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    truth_latest_path = Path(truth_refresh_latest_path).resolve()
    source_latest_path = Path(measurement_sources_latest_path).resolve()
    output_root = Path(output_root).resolve()
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    if not portfolio_id or any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        for character in portfolio_id
    ):
        raise ValueError("portfolio_id is invalid")
    refresh = load_latest_scoring_truth_refresh(truth_latest_path)
    source_manifest = load_latest_calibration_measurement_sources(source_latest_path)
    truth_latest = _read_json(truth_latest_path)
    source_latest = _read_json(source_latest_path)
    refresh_manifest_path = Path(truth_latest["manifest"]["path"]).resolve()
    source_manifest_path = Path(source_latest["manifest"]["path"]).resolve()
    if source_manifest["truth_manifest"]["sha256"] != refresh["source"]["truth_pack"][
        "files"
    ]["manifest.json"]:
        raise ValueError("measurement sources and truth refresh use different truth manifests")
    if source_manifest["feasibility_registry"]["sha256"] != refresh["artifacts"][
        "registry_snapshot"
    ]["sha256"]:
        raise ValueError("measurement sources and truth refresh use different registries")
    run_dir = output_root / portfolio_id
    if run_dir.exists():
        raise FileExistsError(f"calibration portfolio already exists: {run_dir}")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir()
    try:
        sources_dir = run_dir / "sources"
        sources_dir.mkdir()
        snapshots = {
            "registry": sources_dir / "metric-feasibility-pose-wave-v2.json",
            "manual_events": sources_dir / "manual-events.jsonl",
            "manual_semantics": sources_dir / "manual-semantics.jsonl",
            "coach_labels": sources_dir / "coach-labels.jsonl",
            "truth_validation": sources_dir / "truth-validation-report.json",
            "truth_manifest": sources_dir / "truth-manifest.json",
            "measurement_source_manifest": sources_dir
            / "measurement-source-manifest.json",
        }
        upstream_names = {
            "registry": "registry_snapshot",
            "manual_events": "manual_events",
            "manual_semantics": "manual_semantics",
            "coach_labels": "coach_labels",
            "truth_validation": "truth_validation",
        }
        for destination_name, upstream_name in upstream_names.items():
            binding = refresh["artifacts"][upstream_name]
            _copy_bound(
                Path(binding["path"]).resolve(),
                snapshots[destination_name],
                binding["sha256"],
            )
        _copy_bound(
            Path(source_manifest["truth_manifest"]["path"]).resolve(),
            snapshots["truth_manifest"],
            source_manifest["truth_manifest"]["sha256"],
        )
        _copy_bound(
            source_manifest_path,
            snapshots["measurement_source_manifest"],
            source_latest["manifest"]["sha256"],
        )
        all_events = _read_jsonl(snapshots["manual_events"])
        registry = _read_json(snapshots["registry"])
        indicator_ids = [item["indicator_id"] for item in registry["indicators"]]
        manual_feature_artifacts: dict[str, dict[str, dict[str, str]]] = {}
        indicator_feature_paths: list[Path] = []
        video_measurements: list[dict[str, Any]] = []
        manual_feature_total = 0
        manual_indicator_total = 0
        for source in source_manifest["sources"]:
            video_id = source["video_id"]
            video_events = [item for item in all_events if item.get("video_id") == video_id]
            filtered_path = sources_dir / f"{video_id}-manual-events.jsonl"
            _write_jsonl(filtered_path, video_events)
            feature_dir = run_dir / "manual-event-features" / video_id
            result = build_manual_event_features(
                frames_path=source["frames"]["path"],
                primary_timeline_path=source["primary_timeline"]["path"],
                manual_events_path=filtered_path,
                feasibility_registry_path=snapshots["registry"],
                video_id=video_id,
                output_dir=feature_dir,
                pose_model={
                    "backend": source["pose_contract"]["backend"],
                    "runtime": source["pose_contract"]["runtime"],
                    "profile": source["pose_contract"]["model_name"],
                    "model_sha256": source["pose_contract"]["model_sha256"],
                    "native_keypoint_format": source["pose_contract"][
                        "native_keypoint_format"
                    ],
                    "native_keypoint_count": source["pose_contract"][
                        "native_keypoint_count"
                    ],
                },
                source_provenance={
                    "truth_refresh_id": refresh["refresh_id"],
                    "truth_refresh_manifest_sha256": truth_latest["manifest"]["sha256"],
                    "measurement_source_set_id": source_manifest["source_set_id"],
                    "measurement_source_manifest_sha256": source_latest["manifest"][
                        "sha256"
                    ],
                },
            )
            artifacts = {
                "filtered_manual_events": _artifact(filtered_path),
                "summary": _artifact(feature_dir / "summary.json"),
                "events": _artifact(feature_dir / "events.jsonl"),
                "features": _artifact(feature_dir / "features.jsonl"),
                "indicator_features": _artifact(feature_dir / "indicator-features.jsonl"),
                "scores": _artifact(feature_dir / "scores.jsonl"),
            }
            manual_feature_artifacts[video_id] = artifacts
            indicator_feature_paths.append(feature_dir / "indicator-features.jsonl")
            manual_feature_total += len(result["features"])
            manual_indicator_total += len(result["indicator_records"])
            video_measurements.append(
                {
                    "video_id": video_id,
                    "frame_count": source["frame_count"],
                    "frames_sha256": source["frames"]["sha256"],
                    "primary_timeline_sha256": source["primary_timeline"]["sha256"],
                    "manual_event_count": len(video_events),
                    "manual_feature_count": len(result["features"]),
                    "manual_indicator_count": len(result["indicator_records"]),
                    "manual_feature_status": result["summary"]["status"],
                }
            )
        covered_event_ids = {
            (event["video_id"], event["event_id"])
            for source in source_manifest["sources"]
            for event in all_events
            if event.get("video_id") == source["video_id"]
        }
        if len(covered_event_ids) != len(all_events):
            raise ValueError("accepted manual events include a video without measurement source")
        calibration_dir = run_dir / "calibration-dataset"
        dataset = compile_calibration_dataset(
            feasibility_registry_path=snapshots["registry"],
            indicator_feature_paths=indicator_feature_paths,
            manual_events_path=snapshots["manual_events"],
            manual_semantics_path=snapshots["manual_semantics"],
            coach_labels_path=snapshots["coach_labels"],
            truth_manifest_path=snapshots["truth_manifest"],
            truth_validation_report_path=snapshots["truth_validation"],
            output_dir=calibration_dir,
            generated_at=generated_at,
        )
        fit_path = run_dir / "fit-readiness.json"
        fit = _fit_readiness(
            dataset_manifest=dataset,
            prepared_paths=dataset["outputs"]["prepared_by_indicator"],
            output_path=fit_path,
        )
        source_snapshot_artifacts = {
            name: _artifact(path) for name, path in snapshots.items()
        }
        calibration_artifacts = {
            "manifest": _artifact(calibration_dir / "manifest.json"),
            "readiness": _artifact(calibration_dir / "readiness-report.json"),
            "samples": _artifact(calibration_dir / "samples.jsonl"),
            "split_manifest": _artifact(calibration_dir / "split-manifest.json"),
        }
        prepared_artifacts = {
            indicator_id: _artifact(
                Path(dataset["outputs"]["prepared_by_indicator"][indicator_id])
            )
            for indicator_id in indicator_ids
        }
        status = (
            "annotation_required"
            if dataset["status"] == "annotation_required"
            else fit["status"]
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "portfolio_version": PORTFOLIO_VERSION,
            "portfolio_id": portfolio_id,
            "generated_at": generated_at,
            "status": status,
            "source_fingerprint_sha256": _canonical_sha256(
                {
                    "truth_refresh_manifest_sha256": truth_latest["manifest"]["sha256"],
                    "measurement_source_manifest_sha256": source_latest["manifest"][
                        "sha256"
                    ],
                    "registry_sha256": source_snapshot_artifacts["registry"]["sha256"],
                    "manual_events_sha256": source_snapshot_artifacts["manual_events"][
                        "sha256"
                    ],
                    "manual_semantics_sha256": source_snapshot_artifacts[
                        "manual_semantics"
                    ]["sha256"],
                    "coach_labels_sha256": source_snapshot_artifacts["coach_labels"][
                        "sha256"
                    ],
                }
            ),
            "source": {
                "truth_refresh": {
                    "refresh_id": refresh["refresh_id"],
                    "manifest": _artifact(refresh_manifest_path),
                },
                "measurement_sources": {
                    "source_set_id": source_manifest["source_set_id"],
                    "manifest": _artifact(source_manifest_path),
                    "shared_pose_contract": source_manifest["shared_pose_contract"],
                },
            },
            "registry": {
                "version": registry["registry_version"],
                "indicator_ids": indicator_ids,
                "indicator_count": len(indicator_ids),
            },
            "states": {
                "manual_event_features": (
                    "manual_event_features_measured_calibration_required"
                    if all_events
                    else "annotation_required_no_accepted_manual_events"
                ),
                "calibration_dataset": dataset["status"],
                "fit_readiness": fit["status"],
            },
            "counts": {
                "indicators": len(indicator_ids),
                "truth_manifest_videos": source_manifest["video_count"],
                "measurement_source_videos": len(video_measurements),
                "measurement_source_frames": source_manifest["total_frame_count"],
                "accepted_manual_events": len(all_events),
                "manual_feature_records": manual_feature_total,
                "manual_indicator_records": manual_indicator_total,
                "calibration_samples": dataset["counts"]["samples"],
                "prepared_indicators": dataset["counts"]["prepared_indicator_files"],
                "fit_ready_indicators": fit["fit_ready_indicator_count"],
            },
            "video_measurements": video_measurements,
            "indicator_readiness": fit["indicators"],
            "artifacts": {
                "truth_refresh_manifest": _artifact(refresh_manifest_path),
                "measurement_source_manifest": _artifact(source_manifest_path),
                "source_snapshots": source_snapshot_artifacts,
                "manual_event_features_by_video": manual_feature_artifacts,
                "calibration_dataset": calibration_artifacts,
                "prepared_by_indicator": prepared_artifacts,
                "fit_readiness": _artifact(fit_path),
            },
            "safety": {
                "gpu_inference_executed": False,
                "candidate_event_detector_used": False,
                "candidate_boundaries_promoted_to_truth": False,
                "fit_executed": False,
                "candidate_written": False,
                "thresholds_generated": False,
                "model_trained": False,
                "grades_generated": False,
                "automatic_F3_or_F4_promotion": False,
                "empty_truth_interpreted_as_grade_E_or_zero": False,
                "all_truth_videos_have_measurement_sources": True,
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
        manifest_path = run_dir / "portfolio-manifest.json"
        _write_json(manifest_path, manifest)
        validate_scoring_truth_calibration_portfolio(manifest, verify_sources=True)
        latest = {
            "schema_version": SCHEMA_VERSION,
            "latest_version": LATEST_VERSION,
            "updated_at": generated_at,
            "portfolio_id": portfolio_id,
            "manifest": _artifact(manifest_path),
        }
        temporary = output_root / f".latest-{portfolio_id}.tmp"
        _write_json(temporary, latest)
        os.replace(temporary, output_root / "latest.json")
        return manifest
    except Exception:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        raise


def _verify(binding: Mapping[str, Any], name: str) -> Path:
    path = Path(str(binding.get("path", ""))).resolve()
    if not path.is_file() or _sha256_file(path) != str(binding.get("sha256", "")).upper():
        raise ValueError(f"calibration portfolio artifact mismatch: {name}")
    return path


def validate_scoring_truth_calibration_portfolio(
    manifest: Mapping[str, Any], *, verify_sources: bool = False
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get(
        "portfolio_version"
    ) != PORTFOLIO_VERSION:
        raise ValueError("unsupported calibration portfolio")
    if manifest.get("status") not in {
        "annotation_required",
        "partially_prepared_requires_more_truth",
        "all_indicators_prepared_requires_external_protocol",
    }:
        raise ValueError("calibration portfolio status is invalid")
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping):
        raise ValueError("calibration portfolio safety is missing")
    if safety.get("all_truth_videos_have_measurement_sources") is not True:
        raise ValueError("calibration portfolio does not cover all truth videos")
    for field in (
        "gpu_inference_executed",
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
            raise ValueError(f"unsafe calibration portfolio field: {field}")
    if safety.get("maximum_status_without_external_calibration") != "calibration_required":
        raise ValueError("calibration portfolio uncalibrated status is unsafe")
    registry_section = manifest.get("registry")
    if not isinstance(registry_section, Mapping):
        raise ValueError("calibration portfolio registry is missing")
    declared_ids = registry_section.get("indicator_ids")
    if (
        not isinstance(declared_ids, list)
        or not declared_ids
        or len(set(declared_ids)) != len(declared_ids)
        or registry_section.get("indicator_count") != len(declared_ids)
    ):
        raise ValueError("calibration portfolio indicator set is invalid")
    if not verify_sources:
        return
    artifacts = manifest["artifacts"]
    refresh_path = _verify(artifacts["truth_refresh_manifest"], "truth_refresh")
    refresh = _read_json(refresh_path)
    validate_scoring_truth_refresh_manifest(refresh, verify_sources=True)
    source_path = _verify(
        artifacts["measurement_source_manifest"], "measurement_sources"
    )
    measurement_sources = _read_json(source_path)
    validate_calibration_measurement_sources(measurement_sources, verify_sources=True)
    if manifest["source"]["truth_refresh"]["refresh_id"] != refresh["refresh_id"]:
        raise ValueError("calibration portfolio refresh ID mismatch")
    if manifest["source"]["truth_refresh"]["manifest"] != artifacts[
        "truth_refresh_manifest"
    ]:
        raise ValueError("calibration portfolio refresh binding mismatch")
    if (
        manifest["source"]["measurement_sources"]["source_set_id"]
        != measurement_sources["source_set_id"]
    ):
        raise ValueError("calibration portfolio measurement source ID mismatch")
    if manifest["source"]["measurement_sources"]["manifest"] != artifacts[
        "measurement_source_manifest"
    ]:
        raise ValueError("calibration portfolio measurement binding mismatch")
    if manifest["source"]["measurement_sources"]["shared_pose_contract"] != measurement_sources[
        "shared_pose_contract"
    ]:
        raise ValueError("calibration portfolio pose contract mismatch")
    snapshot_paths = {
        name: _verify(binding, f"source_snapshots.{name}")
        for name, binding in artifacts["source_snapshots"].items()
    }
    expected_snapshot_hashes = {
        "registry": refresh["artifacts"]["registry_snapshot"]["sha256"],
        "manual_events": refresh["artifacts"]["manual_events"]["sha256"],
        "manual_semantics": refresh["artifacts"]["manual_semantics"]["sha256"],
        "coach_labels": refresh["artifacts"]["coach_labels"]["sha256"],
        "truth_validation": refresh["artifacts"]["truth_validation"]["sha256"],
        "truth_manifest": measurement_sources["truth_manifest"]["sha256"],
        "measurement_source_manifest": artifacts["measurement_source_manifest"][
            "sha256"
        ],
    }
    for name, expected in expected_snapshot_hashes.items():
        if _sha256_file(snapshot_paths[name]) != str(expected).upper():
            raise ValueError(f"calibration portfolio snapshot mismatch: {name}")
    registry = _read_json(snapshot_paths["registry"])
    indicator_ids = [item["indicator_id"] for item in registry["indicators"]]
    if (
        manifest["registry"]["indicator_ids"] != indicator_ids
        or manifest["registry"]["indicator_count"] != len(indicator_ids)
        or manifest["registry"]["version"] != registry["registry_version"]
    ):
        raise ValueError("calibration portfolio indicator set mismatch")
    all_events = _read_jsonl(snapshot_paths["manual_events"])
    source_by_id = {item["video_id"]: item for item in measurement_sources["sources"]}
    event_keys = [(item.get("video_id"), item.get("event_id")) for item in all_events]
    if len(set(event_keys)) != len(event_keys) or any(
        video_id not in source_by_id for video_id, _ in event_keys
    ):
        raise ValueError("calibration portfolio manual event coverage is invalid")
    feature_bindings = artifacts["manual_event_features_by_video"]
    if set(feature_bindings) != set(source_by_id):
        raise ValueError("calibration portfolio video feature set mismatch")
    replay_video: list[dict[str, Any]] = []
    indicator_sources: list[dict[str, str]] = []
    feature_total = 0
    indicator_total = 0
    for video_id in sorted(source_by_id):
        bindings = feature_bindings[video_id]
        paths = {name: _verify(value, f"{video_id}.{name}") for name, value in bindings.items()}
        expected_events = [item for item in all_events if item.get("video_id") == video_id]
        if _read_jsonl(paths["filtered_manual_events"]) != expected_events:
            raise ValueError(f"calibration portfolio filtered events mismatch: {video_id}")
        summary = _read_json(paths["summary"])
        source = source_by_id[video_id]
        if (
            summary["source_files"]["frames"]["sha256"].upper()
            != source["frames"]["sha256"]
            or summary["source_files"]["primary_timeline"]["sha256"].upper()
            != source["primary_timeline"]["sha256"]
            or summary["safety"]["candidate_event_detector_used"] is not False
        ):
            raise ValueError(f"calibration portfolio manual feature lineage mismatch: {video_id}")
        features = _read_jsonl(paths["features"])
        indicators = _read_jsonl(paths["indicator_features"])
        scores = _read_jsonl(paths["scores"])
        if any(item.get("grade") is not None for item in scores):
            raise ValueError("calibration portfolio contains an uncalibrated grade")
        if any(
            item.get("status") not in {"calibration_required", "unavailable"}
            for item in scores
        ):
            raise ValueError("calibration portfolio contains an unsafe score status")
        feature_total += len(features)
        indicator_total += len(indicators)
        replay_video.append(
            {
                "video_id": video_id,
                "frame_count": source["frame_count"],
                "frames_sha256": source["frames"]["sha256"],
                "primary_timeline_sha256": source["primary_timeline"]["sha256"],
                "manual_event_count": len(expected_events),
                "manual_feature_count": len(features),
                "manual_indicator_count": len(indicators),
                "manual_feature_status": summary["status"],
            }
        )
        indicator_sources.append(bindings["indicator_features"])
    if replay_video != manifest["video_measurements"]:
        raise ValueError("calibration portfolio video measurements differ from replay")
    calibration_paths = {
        name: _verify(binding, f"calibration_dataset.{name}")
        for name, binding in artifacts["calibration_dataset"].items()
    }
    dataset = _read_json(calibration_paths["manifest"])
    if dataset["registry"]["indicator_ids"] != indicator_ids:
        raise ValueError("calibration portfolio dataset indicator set mismatch")
    expected_dataset_sources = {
        "feasibility_registry": artifacts["source_snapshots"]["registry"],
        "manual_events": artifacts["source_snapshots"]["manual_events"],
        "manual_semantics": artifacts["source_snapshots"]["manual_semantics"],
        "coach_labels": artifacts["source_snapshots"]["coach_labels"],
        "truth_manifest": artifacts["source_snapshots"]["truth_manifest"],
        "truth_validation_report": artifacts["source_snapshots"][
            "truth_validation"
        ],
    }
    for name, expected in expected_dataset_sources.items():
        actual = dataset["source_files"][name]
        if (
            actual["path"] != expected["path"]
            or actual["sha256"].upper() != expected["sha256"]
        ):
            raise ValueError(f"calibration portfolio dataset source mismatch: {name}")
    actual_indicator_sources = dataset["source_files"]["indicator_features"]
    if len(actual_indicator_sources) != len(indicator_sources) or any(
        actual["path"] != expected["path"]
        or actual["sha256"].upper() != expected["sha256"]
        for actual, expected in zip(actual_indicator_sources, indicator_sources)
    ):
        raise ValueError("calibration portfolio dataset feature sources mismatch")
    expected_dataset_outputs = {
        "samples": calibration_paths["samples"],
        "split_manifest": calibration_paths["split_manifest"],
        "readiness_report": calibration_paths["readiness"],
    }
    for name, path in expected_dataset_outputs.items():
        if Path(dataset["outputs"][name]).resolve() != path:
            raise ValueError(f"calibration portfolio dataset output mismatch: {name}")
    dataset_safety = dataset["safety"]
    for field in (
        "generated_thresholds",
        "trained_scoring_model",
        "automatic_F3_or_F4_promotion",
        "candidate_events_promoted_to_truth",
        "majority_or_median_grade_resolution",
        "acceptance_thresholds_applied",
    ):
        if dataset_safety.get(field) is not False:
            raise ValueError("calibration portfolio dataset contains an unsafe claim")
    if "prepared_artifact_scope" in dataset_safety:
        if (
            dataset_safety.get("prepared_artifact_scope")
            != "unverified_truth_diagnostic_input"
            or dataset_safety.get("verified_authorized_truth_intake") is not False
        ):
            raise ValueError(
                "calibration portfolio must retain unverified diagnostic truth scope"
            )
    elif dataset.get("status") != "annotation_required":
        raise ValueError(
            "legacy calibration portfolio without truth authority must be annotation_required"
        )
    prepared = artifacts["prepared_by_indicator"]
    if set(prepared) != set(indicator_ids):
        raise ValueError("calibration portfolio prepared set mismatch")
    readiness: list[dict[str, Any]] = []
    for indicator_id in indicator_ids:
        path = _verify(prepared[indicator_id], f"prepared.{indicator_id}")
        payload = _read_json(path)
        audit = validate_prepared_dataset(payload, require_fit_ready=False)
        fit_ready = True
        blocker: str | None = None
        try:
            validate_prepared_dataset(payload, require_fit_ready=True)
        except CalibrationFitError as exc:
            fit_ready = False
            blocker = str(exc)
        readiness.append(
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
    if readiness != manifest["indicator_readiness"]:
        raise ValueError("calibration portfolio readiness differs from replay")
    fit_path = _verify(artifacts["fit_readiness"], "fit_readiness")
    fit = _read_json(fit_path)
    expected_fit_status = (
        "all_indicators_prepared_requires_external_protocol"
        if all(item["fit_ready"] for item in readiness)
        else "partially_prepared_requires_more_truth"
        if any(item["fit_ready"] for item in readiness)
        else "annotation_required"
    )
    if (
        fit["indicators"] != readiness
        or any(fit["safety"].values())
        or fit["status"] != expected_fit_status
        or fit["fit_ready_indicator_count"]
        != sum(item["fit_ready"] for item in readiness)
    ):
        raise ValueError("calibration portfolio fit report mismatch")
    expected_states = {
        "manual_event_features": (
            "manual_event_features_measured_calibration_required"
            if all_events
            else "annotation_required_no_accepted_manual_events"
        ),
        "calibration_dataset": dataset["status"],
        "fit_readiness": fit["status"],
    }
    if manifest.get("states") != expected_states:
        raise ValueError("calibration portfolio states differ from replay")
    expected_status = (
        "annotation_required"
        if dataset["status"] == "annotation_required"
        else fit["status"]
    )
    if manifest.get("status") != expected_status:
        raise ValueError("calibration portfolio status differs from replay")
    _verify(artifacts["index_html"], "index_html")
    counts = manifest["counts"]
    expected_counts = {
        "indicators": len(indicator_ids),
        "truth_manifest_videos": measurement_sources["video_count"],
        "measurement_source_videos": len(source_by_id),
        "measurement_source_frames": measurement_sources["total_frame_count"],
        "accepted_manual_events": len(all_events),
        "manual_feature_records": feature_total,
        "manual_indicator_records": indicator_total,
        "calibration_samples": len(_read_jsonl(calibration_paths["samples"])),
        "prepared_indicators": len(prepared),
        "fit_ready_indicators": sum(item["fit_ready"] for item in readiness),
    }
    if counts != expected_counts:
        raise ValueError("calibration portfolio counts differ from replay")
    expected_fingerprint = _canonical_sha256(
        {
            "truth_refresh_manifest_sha256": artifacts["truth_refresh_manifest"][
                "sha256"
            ],
            "measurement_source_manifest_sha256": artifacts[
                "measurement_source_manifest"
            ]["sha256"],
            "registry_sha256": artifacts["source_snapshots"]["registry"]["sha256"],
            "manual_events_sha256": artifacts["source_snapshots"]["manual_events"][
                "sha256"
            ],
            "manual_semantics_sha256": artifacts["source_snapshots"][
                "manual_semantics"
            ]["sha256"],
            "coach_labels_sha256": artifacts["source_snapshots"]["coach_labels"][
                "sha256"
            ],
        }
    )
    if manifest["source_fingerprint_sha256"] != expected_fingerprint:
        raise ValueError("calibration portfolio source fingerprint mismatch")


def load_latest_scoring_truth_calibration_portfolio(
    latest_path: str | Path,
) -> dict[str, Any]:
    latest = _read_json(Path(latest_path).resolve())
    if latest.get("schema_version") != SCHEMA_VERSION or latest.get(
        "latest_version"
    ) != LATEST_VERSION:
        raise ValueError("unsupported calibration portfolio latest pointer")
    manifest_path = _verify(latest["manifest"], "latest.manifest")
    manifest = _read_json(manifest_path)
    if manifest.get("portfolio_id") != latest.get("portfolio_id"):
        raise ValueError("calibration portfolio latest ID mismatch")
    validate_scoring_truth_calibration_portfolio(manifest, verify_sources=True)
    return manifest


__all__ = [
    "LATEST_VERSION",
    "PORTFOLIO_VERSION",
    "build_scoring_truth_calibration_portfolio",
    "load_latest_scoring_truth_calibration_portfolio",
    "validate_scoring_truth_calibration_portfolio",
]
