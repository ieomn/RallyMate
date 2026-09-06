from __future__ import annotations

import html
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from rallymate_scoring.calibration_measurement_sources import (
    load_latest_calibration_measurement_sources,
)
from rallymate_vision.validation import validate_run_artifacts


SCHEMA_VERSION = "1.0.0"
COVERAGE_VERSION = "multivideo-indicator-calculation-coverage-v1.0.0"
LATEST_VERSION = "multivideo-indicator-calculation-coverage-latest-v1.0.0"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL record must be an object: {path}:{line_number}")
            values.append(value)
    return values


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest().upper()


def _artifact(path: Path) -> dict[str, str]:
    path = path.resolve()
    return {"path": str(path), "sha256": _sha256_file(path)}


def _verify_artifact(binding: Mapping[str, Any], name: str) -> Path:
    path = Path(str(binding.get("path", ""))).resolve()
    if not path.is_file() or _sha256_file(path) != str(
        binding.get("sha256", "")
    ).upper():
        raise ValueError(f"multivideo coverage artifact mismatch: {name}")
    return path


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _feature_vector(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for feature in record["features"]:
        if feature.get("valid") is not True or feature.get("value") is None:
            raise ValueError("selected measured vector contains an invalid feature")
        result.append(
            {
                "feature_name": feature["feature_name"],
                "feature_version": feature["feature_version"],
                "value": feature["value"],
                "unit": feature["unit"],
                "confidence": feature["confidence"],
                "valid": True,
                "reason": feature["reason"],
                "source_frames": feature["source_frames"],
            }
        )
    return result


def _replay_video(
    *,
    report_path: Path,
    source: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    report = _read_json(report_path)
    video_id = str(source["video_id"])
    if report.get("video_id") != video_id:
        raise ValueError(f"coverage report video mismatch: {video_id}")
    if report.get("status") != "passed_real_video_features_no_calibration":
        raise ValueError(f"coverage source report is not safe: {video_id}")
    assertions = report.get("assertions", {})
    if (
        assertions.get("all_registry_indicators_present") is not True
        or assertions.get("all_grades_null") is not True
        or assertions.get("all_threshold_versions_null") is not True
        or assertions.get("fake_thresholds_generated") is not False
        or int(assertions.get("grade_count", -1)) != 0
    ):
        raise ValueError(f"coverage source report has an unsafe assertion: {video_id}")
    run_dir = Path(report["artifacts"]["directory"]).resolve()
    if validate_run_artifacts(run_dir).get("status") != "passed":
        raise ValueError(f"coverage source run bundle failed validation: {video_id}")
    summary_path = run_dir / "scoring-loop-summary.json"
    summary = _read_json(summary_path)
    if summary != report["summary"] or summary.get("video_id") != video_id:
        raise ValueError(f"coverage source summary mismatch: {video_id}")
    registry_ids = [item["indicator_id"] for item in registry["indicators"]]
    if report["indicator_scope"]["indicator_ids"] != sorted(registry_ids):
        raise ValueError(f"coverage source indicator set mismatch: {video_id}")
    if (
        summary["model_versions"]["feasibility_registry"]
        != registry["registry_version"]
        or summary["model_versions"]["primary_player"]
        != source["primary_player_version"]
        or summary["model_versions"]["pose_model_sha256"]
        != source["pose_contract"]["model_sha256"]
        or summary["model_versions"]["native_keypoint_format"]
        != source["pose_contract"]["native_keypoint_format"]
        or summary["model_versions"]["native_keypoint_count"]
        != source["pose_contract"]["native_keypoint_count"]
    ):
        raise ValueError(f"coverage source runtime contract mismatch: {video_id}")
    provenance = summary["provenance"]
    if (
        provenance["frames_sha256"] != source["frames"]["sha256"]
        or provenance["primary_timeline_sha256"]
        != source["primary_timeline"]["sha256"]
        or provenance["video_sha256"] != source["source_video"]["sha256"]
        or provenance["frame_window"]["frame_count"] != source["frame_count"]
    ):
        raise ValueError(f"coverage source lineage mismatch: {video_id}")
    artifact_paths = {
        "events": run_dir / "events.jsonl",
        "indicator_features": run_dir / "indicator-features.jsonl",
        "scores": run_dir / "scores.jsonl",
    }
    for name, filename in summary["artifacts"].items():
        artifact_path = run_dir / filename
        if _sha256_file(artifact_path) != summary["artifact_sha256"][name]:
            raise ValueError(f"coverage source summary hash mismatch: {video_id}.{name}")
    events = _read_jsonl(artifact_paths["events"])
    indicators = _read_jsonl(artifact_paths["indicator_features"])
    scores = _read_jsonl(artifact_paths["scores"])
    event_by_id = {item["event_id"]: item for item in events}
    if len(event_by_id) != len(events):
        raise ValueError(f"coverage source event IDs are not unique: {video_id}")
    indicator_by_key = {
        (item["indicator_id"], item["event_id"]): item for item in indicators
    }
    score_by_key = {(item["indicator_id"], item["event_id"]): item for item in scores}
    if (
        len(indicator_by_key) != len(indicators)
        or len(score_by_key) != len(scores)
        or set(indicator_by_key) != set(score_by_key)
    ):
        raise ValueError(f"coverage source indicator/score keys mismatch: {video_id}")
    if any(item["event_id"] not in event_by_id for item in indicators):
        raise ValueError(f"coverage source indicator has no event: {video_id}")
    if any(
        score.get("grade") is not None
        or score.get("threshold_version") is not None
        or score.get("status") not in {"calibration_required", "unavailable"}
        for score in scores
    ):
        raise ValueError(f"coverage source emitted grade/threshold/unsafe status: {video_id}")
    for key, record in indicator_by_key.items():
        score = score_by_key[key]
        if (
            record["scoring_status"] != score["status"]
            or record["grade"] != score["grade"]
        ):
            raise ValueError(f"coverage source indicator/score content mismatch: {video_id}")
    per_indicator: dict[str, dict[str, Any]] = {}
    for indicator in registry["indicators"]:
        indicator_id = indicator["indicator_id"]
        rows = [item for item in indicators if item["indicator_id"] == indicator_id]
        score_rows = [score_by_key[(indicator_id, item["event_id"])] for item in rows]
        measured_rows = [item for item in rows if item["feature_status"] == "measured"]
        failure_reasons: Counter[str] = Counter()
        for item in rows:
            if item["feature_status"] == "measured":
                continue
            failure_reasons.update(
                str(feature["reason"])
                for feature in item["features"]
                if feature.get("valid") is not True
            )
        first_measured = None
        if measured_rows:
            selected = min(
                measured_rows,
                key=lambda item: (
                    int(event_by_id[item["event_id"]]["start_ms"]),
                    str(item["event_id"]),
                ),
            )
            event = event_by_id[selected["event_id"]]
            first_measured = {
                "selection_rule": "earliest_start_ms_then_event_id_no_feature_value_ranking",
                "event_id": selected["event_id"],
                "event_code": selected["event_code"],
                "person_track_id": selected["person_track_id"],
                "start_ms": event["start_ms"],
                "end_ms": event["end_ms"],
                "features": _feature_vector(selected),
            }
        per_indicator[indicator_id] = {
            "video_id": video_id,
            "candidate_instances": len(rows),
            "measured_feature_vectors": len(measured_rows),
            "unavailable_feature_vectors": len(rows) - len(measured_rows),
            "measurement_rate": (
                round(len(measured_rows) / len(rows), 8) if rows else None
            ),
            "score_status_counts": dict(
                sorted(Counter(item["status"] for item in score_rows).items())
            ),
            "measurement_failure_reason_counts": dict(sorted(failure_reasons.items())),
            "first_measured_evidence": first_measured,
        }
    event_counts = Counter(item["event_code"] for item in events)
    score_counts = Counter(item["status"] for item in scores)
    quality_counts = Counter(
        item["quality_gate"]["status"] for item in indicators
    )
    score_reason_counts: Counter[str] = Counter()
    scoring_block_flag_counts: Counter[str] = Counter()
    for item in indicators:
        score_reason_counts.update(str(value) for value in item["reason_codes"])
        scoring_block_flag_counts.update(
            str(value) for value in item["quality_gate"]["scoring_block_flags"]
        )
    row = {
        "video_id": video_id,
        "frame_count": source["frame_count"],
        "duration_ms": source["last_timestamp_ms"] - source["first_timestamp_ms"],
        "candidate_event_counts": dict(sorted(event_counts.items())),
        "candidate_event_count": len(events),
        "indicator_event_instances": len(indicators),
        "measured_feature_vectors": sum(
            item["feature_status"] == "measured" for item in indicators
        ),
        "unavailable_feature_vectors": sum(
            item["feature_status"] != "measured" for item in indicators
        ),
        "indicators_with_measured_vector": sum(
            per_indicator[indicator_id]["measured_feature_vectors"] > 0
            for indicator_id in registry_ids
        ),
        "score_status_counts": dict(sorted(score_counts.items())),
        "quality_gate_status_counts": dict(sorted(quality_counts.items())),
        "score_reason_counts": dict(sorted(score_reason_counts.items())),
        "scoring_block_flag_counts": dict(sorted(scoring_block_flag_counts.items())),
        "grade_count": 0,
        "threshold_version_count": 0,
    }
    artifacts = {
        "source_report": _artifact(report_path),
        "summary": _artifact(summary_path),
        "events": _artifact(artifact_paths["events"]),
        "indicator_features": _artifact(artifact_paths["indicator_features"]),
        "scores": _artifact(artifact_paths["scores"]),
        "scoring_loop_report": _artifact(run_dir / "scoring-loop-report.html"),
    }
    return row, per_indicator, artifacts


def _assemble(
    *,
    measurement_manifest: Mapping[str, Any],
    measurement_manifest_binding: Mapping[str, Any],
    registry: Mapping[str, Any],
    registry_binding: Mapping[str, Any],
    report_by_video: Mapping[str, Path],
) -> dict[str, Any]:
    sources = {item["video_id"]: item for item in measurement_manifest["sources"]}
    if set(report_by_video) != set(sources):
        raise ValueError("coverage reports must exactly cover measurement source videos")
    registry_ids = [item["indicator_id"] for item in registry["indicators"]]
    video_rows: list[dict[str, Any]] = []
    replay_by_video: dict[str, dict[str, dict[str, Any]]] = {}
    source_artifacts: dict[str, dict[str, dict[str, str]]] = {}
    for video_id in sorted(sources):
        row, per_indicator, artifacts = _replay_video(
            report_path=report_by_video[video_id],
            source=sources[video_id],
            registry=registry,
        )
        video_rows.append(row)
        replay_by_video[video_id] = per_indicator
        source_artifacts[video_id] = artifacts
    indicator_rows: list[dict[str, Any]] = []
    for indicator in registry["indicators"]:
        indicator_id = indicator["indicator_id"]
        by_video = [replay_by_video[video_id][indicator_id] for video_id in sorted(sources)]
        measured = sum(item["measured_feature_vectors"] for item in by_video)
        total = sum(item["candidate_instances"] for item in by_video)
        score_counts: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        for item in by_video:
            score_counts.update(item["score_status_counts"])
            failures.update(item["measurement_failure_reason_counts"])
        indicator_rows.append(
            {
                "indicator_id": indicator_id,
                "feasibility_level": indicator["feasibility_level"],
                "required_events": indicator["required_events"],
                "measurement_features": indicator.get(
                    "measurement_features", indicator["required_features"]
                ),
                "candidate_instances": total,
                "measured_feature_vectors": measured,
                "unavailable_feature_vectors": total - measured,
                "measurement_rate": round(measured / total, 8) if total else None,
                "videos_with_measured_vector": sum(
                    item["measured_feature_vectors"] > 0 for item in by_video
                ),
                "measured_in_every_video": all(
                    item["measured_feature_vectors"] > 0 for item in by_video
                ),
                "score_status_counts": dict(sorted(score_counts.items())),
                "measurement_failure_reason_counts": dict(sorted(failures.items())),
                "by_video": by_video,
            }
        )
    total_events = sum(item["candidate_event_count"] for item in video_rows)
    total_instances = sum(item["indicator_event_instances"] for item in video_rows)
    total_measured = sum(item["measured_feature_vectors"] for item in video_rows)
    score_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    for item in video_rows:
        score_counts.update(item["score_status_counts"])
        quality_counts.update(item["quality_gate_status_counts"])
        reason_counts.update(item["score_reason_counts"])
        flag_counts.update(item["scoring_block_flag_counts"])
    return {
        "source": {
            "measurement_sources": dict(measurement_manifest_binding),
            "source_set_id": measurement_manifest["source_set_id"],
            "registry": dict(registry_binding),
            "registry_version": registry["registry_version"],
            "source_reports": source_artifacts,
        },
        "scope": {
            "video_ids": sorted(sources),
            "indicator_ids": registry_ids,
            "event_codes": sorted(
                {str(value).split(".", 1)[0] for value in registry["scope"]["events"]}
            ),
            "pose_contract": measurement_manifest["shared_pose_contract"],
            "primary_player_version": measurement_manifest[
                "required_primary_player_version"
            ],
        },
        "counts": {
            "videos": len(video_rows),
            "frames": sum(item["frame_count"] for item in video_rows),
            "candidate_events": total_events,
            "indicator_event_instances": total_instances,
            "measured_feature_vectors": total_measured,
            "unavailable_feature_vectors": total_instances - total_measured,
            "indicators": len(registry_ids),
            "indicators_with_measured_vector": sum(
                item["measured_feature_vectors"] > 0 for item in indicator_rows
            ),
            "indicators_measured_in_every_video": sum(
                item["measured_in_every_video"] for item in indicator_rows
            ),
            "score_status_counts": dict(sorted(score_counts.items())),
            "quality_gate_status_counts": dict(sorted(quality_counts.items())),
            "score_reason_counts": dict(sorted(reason_counts.items())),
            "scoring_block_flag_counts": dict(sorted(flag_counts.items())),
            "grade_count": 0,
            "threshold_version_count": 0,
        },
        "videos": video_rows,
        "indicators": indicator_rows,
    }


def _render_index(manifest: Mapping[str, Any], output_path: Path) -> None:
    video_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}/{}</td><td>{}/{}</td></tr>".format(
            html.escape(item["video_id"]),
            item["frame_count"],
            item["candidate_event_count"],
            item["measured_feature_vectors"],
            item["indicator_event_instances"],
            item["score_status_counts"].get("calibration_required", 0),
            item["score_status_counts"].get("unavailable", 0),
        )
        for item in manifest["videos"]
    )
    indicator_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}/{}</td><td>{}/{}</td><td>{}</td></tr>".format(
            html.escape(item["indicator_id"]),
            html.escape(item["feasibility_level"]),
            item["measured_feature_vectors"],
            item["candidate_instances"],
            item["videos_with_measured_vector"],
            manifest["counts"]["videos"],
            html.escape(
                ", ".join(
                    f"{key}:{value}"
                    for key, value in item["measurement_failure_reason_counts"].items()
                )
                or "—"
            ),
        )
        for item in manifest["indicators"]
    )
    output_path.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>RallyMate M50 多视频指标计算覆盖</title><style>body{{font:15px/1.55 system-ui;margin:28px;max-width:1280px}}table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #bbb;padding:6px;text-align:left}}code{{word-break:break-all}}.warn{{background:#fff4d6;padding:12px}}.good{{background:#e9f8ee;padding:12px}}</style></head><body>
<h1>M50：三视频 13 指标计算覆盖</h1>
<p class="good">三段共 {manifest['counts']['frames']} 帧、{manifest['counts']['candidate_events']} 个规则候选事件、{manifest['counts']['indicator_event_instances']} 条指标事件记录；{manifest['counts']['indicators_measured_in_every_video']}/{manifest['counts']['indicators']} 项在每段视频都至少有一个完整 Pose 测量向量。</p>
<p class="warn">候选事件没有人工真值，测量覆盖不是准确率。当前 A～E={manifest['counts']['grade_count']}、threshold={manifest['counts']['threshold_version_count']}；正式评分仍只允许 calibration_required / unavailable。</p>
<h2>逐视频</h2><table><thead><tr><th>视频</th><th>帧</th><th>候选事件</th><th>特征 measured/实例</th><th>calibration/unavailable</th></tr></thead><tbody>{video_rows}</tbody></table>
<h2>逐指标</h2><table><thead><tr><th>指标</th><th>成熟度</th><th>特征 measured/实例</th><th>有测量视频</th><th>特征失败原因</th></tr></thead><tbody>{indicator_rows}</tbody></table>
</body></html>""",
        encoding="utf-8",
    )


def build_multivideo_indicator_calculation_coverage(
    *,
    measurement_sources_latest_path: str | Path,
    feasibility_registry_path: str | Path,
    source_report_paths: Iterable[str | Path],
    output_root: str | Path,
    coverage_id: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    latest_path = Path(measurement_sources_latest_path).resolve()
    registry_path = Path(feasibility_registry_path).resolve()
    output_root = Path(output_root).resolve()
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    if not coverage_id or any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        for character in coverage_id
    ):
        raise ValueError("coverage_id is invalid")
    run_dir = output_root / coverage_id
    if run_dir.exists():
        raise FileExistsError(f"multivideo coverage already exists: {run_dir}")
    measurement_manifest = load_latest_calibration_measurement_sources(latest_path)
    latest = _read_json(latest_path)
    measurement_binding = latest["manifest"]
    registry = _read_json(registry_path)
    registry_binding = _artifact(registry_path)
    if (
        registry_binding["sha256"]
        != measurement_manifest["feasibility_registry"]["sha256"]
        or registry["registry_version"]
        != measurement_manifest["feasibility_registry"]["version"]
    ):
        raise ValueError("coverage registry differs from measurement source registry")
    reports = [Path(value).resolve() for value in source_report_paths]
    report_by_video: dict[str, Path] = {}
    for path in reports:
        report = _read_json(path)
        video_id = str(report.get("video_id", ""))
        if not video_id or video_id in report_by_video:
            raise ValueError("coverage source report video IDs must be non-empty and unique")
        report_by_video[video_id] = path
    assembled = _assemble(
        measurement_manifest=measurement_manifest,
        measurement_manifest_binding=measurement_binding,
        registry=registry,
        registry_binding=registry_binding,
        report_by_video=report_by_video,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir()
    try:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "coverage_version": COVERAGE_VERSION,
            "coverage_id": coverage_id,
            "generated_at": generated_at,
            "status": "all_registry_indicators_measured_in_every_video_candidate_events_unvalidated",
            **assembled,
            "source_fingerprint_sha256": _canonical_sha256(
                {
                    "measurement_sources": measurement_binding["sha256"],
                    "registry": registry_binding["sha256"],
                    "reports": {
                        video_id: _sha256_file(path)
                        for video_id, path in sorted(report_by_video.items())
                    },
                }
            ),
            "safety": {
                "gpu_inference_executed": False,
                "existing_pose_frames_reused": True,
                "candidate_events_are_ground_truth": False,
                "event_accuracy_claim": False,
                "feature_accuracy_claim": False,
                "formal_score_claim": False,
                "cross_video_feature_value_pooling": False,
                "cross_model_cherry_picking": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "automatic_F3_or_F4_promotion": False,
                "measurement_success_is_accuracy": False,
                "maximum_status_without_calibration": "calibration_required_or_unavailable",
            },
        }
        index_path = run_dir / "index.html"
        manifest["artifacts"] = {
            "index_html": {"path": str(index_path), "sha256": "0" * 64}
        }
        _render_index(manifest, index_path)
        manifest["artifacts"]["index_html"] = _artifact(index_path)
        manifest_path = run_dir / "coverage.json"
        _write_json(manifest_path, manifest)
        validate_multivideo_indicator_calculation_coverage(
            manifest, verify_sources=True
        )
        latest_payload = {
            "schema_version": SCHEMA_VERSION,
            "latest_version": LATEST_VERSION,
            "updated_at": generated_at,
            "coverage_id": coverage_id,
            "manifest": _artifact(manifest_path),
        }
        temporary = output_root / f".latest-{coverage_id}.tmp"
        _write_json(temporary, latest_payload)
        os.replace(temporary, output_root / "latest.json")
        return manifest
    except Exception:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        raise


def validate_multivideo_indicator_calculation_coverage(
    manifest: Mapping[str, Any], *, verify_sources: bool = False
) -> None:
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("coverage_version") != COVERAGE_VERSION
    ):
        raise ValueError("unsupported multivideo calculation coverage")
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping):
        raise ValueError("multivideo calculation coverage safety is missing")
    if safety.get("existing_pose_frames_reused") is not True:
        raise ValueError("multivideo calculation coverage reuse claim is missing")
    for name in (
        "gpu_inference_executed",
        "candidate_events_are_ground_truth",
        "event_accuracy_claim",
        "feature_accuracy_claim",
        "formal_score_claim",
        "cross_video_feature_value_pooling",
        "cross_model_cherry_picking",
        "grades_generated",
        "thresholds_generated",
        "automatic_F3_or_F4_promotion",
        "measurement_success_is_accuracy",
    ):
        if safety.get(name) is not False:
            raise ValueError(f"unsafe multivideo calculation coverage claim: {name}")
    if safety.get("maximum_status_without_calibration") != (
        "calibration_required_or_unavailable"
    ):
        raise ValueError("multivideo calculation coverage status ceiling is unsafe")
    counts = manifest.get("counts", {})
    if (
        counts.get("indicators", 0) < 1
        or counts.get("indicators_with_measured_vector") != counts.get("indicators")
        or counts.get("indicators_measured_in_every_video") != counts.get("indicators")
        or counts.get("grade_count") != 0
        or counts.get("threshold_version_count") != 0
    ):
        raise ValueError("multivideo calculation coverage completeness is invalid")
    if not verify_sources:
        return
    source = manifest["source"]
    measurement_path = _verify_artifact(
        source["measurement_sources"], "measurement_sources"
    )
    measurement_manifest = _read_json(measurement_path)
    registry_path = _verify_artifact(source["registry"], "registry")
    registry = _read_json(registry_path)
    report_by_video = {
        video_id: _verify_artifact(bindings["source_report"], f"{video_id}.report")
        for video_id, bindings in source["source_reports"].items()
    }
    assembled = _assemble(
        measurement_manifest=measurement_manifest,
        measurement_manifest_binding=source["measurement_sources"],
        registry=registry,
        registry_binding=source["registry"],
        report_by_video=report_by_video,
    )
    for name in ("source", "scope", "counts", "videos", "indicators"):
        if manifest.get(name) != assembled[name]:
            raise ValueError(f"multivideo calculation coverage differs from replay: {name}")
    expected_fingerprint = _canonical_sha256(
        {
            "measurement_sources": source["measurement_sources"]["sha256"],
            "registry": source["registry"]["sha256"],
            "reports": {
                video_id: bindings["source_report"]["sha256"]
                for video_id, bindings in sorted(source["source_reports"].items())
            },
        }
    )
    if manifest.get("source_fingerprint_sha256") != expected_fingerprint:
        raise ValueError("multivideo calculation coverage fingerprint mismatch")
    _verify_artifact(manifest["artifacts"]["index_html"], "index_html")


def load_latest_multivideo_indicator_calculation_coverage(
    latest_path: str | Path,
) -> dict[str, Any]:
    latest = _read_json(Path(latest_path).resolve())
    if (
        latest.get("schema_version") != SCHEMA_VERSION
        or latest.get("latest_version") != LATEST_VERSION
    ):
        raise ValueError("unsupported multivideo calculation coverage latest pointer")
    manifest_path = _verify_artifact(latest["manifest"], "latest.manifest")
    manifest = _read_json(manifest_path)
    if manifest.get("coverage_id") != latest.get("coverage_id"):
        raise ValueError("multivideo calculation coverage latest ID mismatch")
    validate_multivideo_indicator_calculation_coverage(manifest, verify_sources=True)
    return manifest


__all__ = [
    "COVERAGE_VERSION",
    "LATEST_VERSION",
    "build_multivideo_indicator_calculation_coverage",
    "load_latest_multivideo_indicator_calculation_coverage",
    "validate_multivideo_indicator_calculation_coverage",
]
