from __future__ import annotations

import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from rallymate_evaluation.small_roi_recovery import (
    feature_vector_transitions,
    operational_measurement_transitions,
    select_small_roi_targets,
)


SCHEMA_VERSION = "1.0.0"
REPORT_VERSION = "multivideo-measurement-recovery-experiment-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    path = path.resolve()
    return {"path": str(path), "sha256": _sha256(path)}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"JSONL must contain non-empty objects: {path}")
    return rows


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_bound_source(binding: Mapping[str, Any], *, label: str) -> Path:
    path = Path(str(binding.get("path", "")))
    expected = str(binding.get("sha256", "")).upper()
    if not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"{label} source hash mismatch")
    return path


def _validate_gap_audit(
    report: Mapping[str, Any], report_path: Path
) -> dict[str, Any]:
    if report.get("schema_version") != "1.1.0" or report.get(
        "report_version"
    ) != "feature-observation-gap-audit-v1.1.0":
        raise ValueError("measurement recovery requires gap audit v1.1")
    assertions = report.get("assertions", {})
    required_true = (
        "required_feature_contract_matches_registry",
        "compact_features_match_full_features",
        "quality_gates_match_event_and_registry",
        "feature_status_respects_vector_and_measurement_gate",
        "human_ground_truth_still_required",
    )
    if any(assertions.get(field) is not True for field in required_true):
        raise ValueError("gap audit lacks a required source-replay assertion")
    prohibited = (
        "measurement_gate_modified",
        "scoring_gate_modified",
        "automatic_profile_fallback_enabled",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    )
    if any(assertions.get(field) is not False for field in prohibited):
        raise ValueError("gap audit contains an unsafe claim")
    source = report.get("source", {})
    for field in (
        "indicator_features",
        "features",
        "events",
        "summary",
        "registry",
    ):
        _validate_bound_source(source.get(field, {}), label=f"gap audit {field}")
    indicator_path = Path(source["indicator_features"]["path"])
    rows = _load_jsonl(indicator_path)
    derived = Counter()
    unavailable_identities: set[tuple[str, str]] = set()
    for row in rows:
        vector_complete = all(
            item.get("valid") is True for item in row.get("features", [])
        )
        hard_fail = row.get("quality_gate", {}).get("hard_fail") is True
        status = str(row.get("feature_status"))
        expected = "measured" if vector_complete and not hard_fail else "unavailable"
        if status != expected:
            raise ValueError("indicator feature status violates vector/gate semantics")
        derived[f"feature_{status}"] += 1
        derived[
            "feature_vector_complete" if vector_complete else "feature_vector_incomplete"
        ] += 1
        if hard_fail:
            derived["measurement_hard_fail"] += 1
            derived[
                "measurement_hard_fail_only"
                if vector_complete
                else "measurement_hard_fail_with_incomplete"
            ] += 1
        if status == "unavailable":
            unavailable_identities.add(
                (str(row["event_id"]), str(row["indicator_id"]))
            )
    declared = report.get("counts", {})
    expected_counts = {
        "indicator_record_count": len(rows),
        "feature_measured_indicator_count": derived["feature_measured"],
        "feature_unavailable_indicator_count": derived["feature_unavailable"],
        "feature_vector_complete_indicator_count": derived[
            "feature_vector_complete"
        ],
        "feature_vector_incomplete_indicator_count": derived[
            "feature_vector_incomplete"
        ],
        "measurement_hard_fail_indicator_count": derived[
            "measurement_hard_fail"
        ],
        "measurement_hard_fail_only_indicator_count": derived[
            "measurement_hard_fail_only"
        ],
        "measurement_hard_fail_with_incomplete_vector_count": derived[
            "measurement_hard_fail_with_incomplete"
        ],
    }
    if any(int(declared.get(key, -1)) != value for key, value in expected_counts.items()):
        raise ValueError("gap audit counts differ from source indicator features")
    reported_identities = {
        (str(event["event_id"]), str(item["indicator_id"]))
        for event in report.get("event_failures", [])
        for item in event.get("indicator_failures", [])
    }
    if reported_identities != unavailable_identities:
        raise ValueError("gap audit event failures do not cover unavailable records")
    summary = _load_json(Path(source["summary"]["path"]))
    if str(summary.get("video_id")) != str(source.get("video_id")):
        raise ValueError("gap audit video differs from scoring summary")
    return {
        "report_path": report_path.resolve(),
        "report": dict(report),
        "indicator_rows": rows,
        "summary": summary,
    }


def _validate_experiment(
    report: Mapping[str, Any], report_path: Path
) -> dict[str, Any]:
    if report.get("schema_version") != "1.1.0" or report.get(
        "experiment_version"
    ) != "small-roi-pose-recovery-v1.1.0":
        raise ValueError("measurement recovery requires small ROI experiment v1.1")
    if report.get("status") != "experimental_observability_only_not_production":
        raise ValueError("small ROI experiment status is unsafe")
    safety = report.get("safety", {})
    prohibited = (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    )
    if any(safety.get(field) is not False for field in prohibited):
        raise ValueError("small ROI experiment contains an unsafe claim")
    if safety.get("current_production_default_unchanged") is not True or safety.get(
        "feature_vector_status_is_not_operational_measurement_status"
    ) is not True:
        raise ValueError("small ROI experiment does not preserve production semantics")
    source = report.get("source", {})
    for field in (
        "video",
        "frames",
        "primary_timeline",
        "events",
        "indicator_features",
        "summary",
        "registry",
        "gap_audit",
        "pose_model",
        "pose_config",
    ):
        _validate_bound_source(source.get(field, {}), label=f"experiment {field}")
    artifacts = report.get("artifacts", {})
    comparison_path = _validate_bound_source(
        artifacts.get("fixed_boundary_comparison", {}),
        label="experiment fixed boundary comparison",
    )
    _validate_bound_source(
        artifacts.get("experimental_frames", {}),
        label="experiment frames",
    )
    comparison = _load_json(comparison_path)
    order = [str(item) for item in comparison.get("model_order", [])]
    current = str(source.get("current_profile", ""))
    if len(order) != 2 or order[0] != current:
        raise ValueError("small ROI comparison model order is invalid")
    rows = _load_jsonl(Path(source["indicator_features"]["path"]))
    expected_vector = feature_vector_transitions(comparison, current, order[1])
    expected_operational = operational_measurement_transitions(
        comparison, current, order[1], rows
    )
    if _canonical(expected_vector) != _canonical(report.get("feature_vector_impact")):
        raise ValueError("small ROI feature-vector impact did not replay")
    if _canonical(expected_operational) != _canonical(
        report.get("operational_measurement_impact")
    ):
        raise ValueError("small ROI operational impact did not replay")
    if expected_vector["regressed_indicator_instance_count"] or expected_operational[
        "regressed_indicator_instance_count"
    ]:
        raise ValueError("small ROI experiment contains a regression")
    summary = _load_json(Path(source["summary"]["path"]))
    return {
        "report_path": report_path.resolve(),
        "report": dict(report),
        "summary": summary,
    }


def build_multivideo_measurement_recovery_report(
    *,
    recovery_id: str,
    gap_audit_paths: Sequence[Path],
    experiment_report_paths: Sequence[Path],
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not recovery_id:
        raise ValueError("recovery_id must be non-empty")
    gaps: dict[str, dict[str, Any]] = {}
    for path in gap_audit_paths:
        payload = _load_json(path)
        validated = _validate_gap_audit(payload, path)
        video_id = str(validated["summary"]["video_id"])
        if video_id in gaps:
            raise ValueError("gap audit videos must be unique")
        gaps[video_id] = validated
    if not gaps:
        raise ValueError("at least one gap audit is required")
    experiments: dict[str, dict[str, Any]] = {}
    for path in experiment_report_paths:
        payload = _load_json(path)
        validated = _validate_experiment(payload, path)
        video_id = str(validated["summary"]["video_id"])
        if video_id in experiments or video_id not in gaps:
            raise ValueError("experiment videos must be unique gap-audit members")
        experiments[video_id] = validated

    registry_identities = {
        (
            str(item["report"]["source"]["registry_version"]),
            str(item["report"]["source"]["registry"]["sha256"]).upper(),
        )
        for item in gaps.values()
    }
    profiles = {
        str(item["report"]["source"]["current_profile"])
        for item in gaps.values()
    }
    if len(registry_identities) != 1 or len(profiles) != 1:
        raise ValueError("all videos must share one registry and Pose profile")
    registry_version, registry_sha = next(iter(registry_identities))
    current_profile = next(iter(profiles))

    baseline = Counter()
    projected = Counter()
    indicator_recoveries: Counter[str] = Counter()
    preflight_total = Counter()
    video_rows: list[dict[str, Any]] = []
    gap_sources: list[dict[str, Any]] = []
    experiment_sources: list[dict[str, Any]] = []
    for video_id in sorted(gaps):
        gap = gaps[video_id]
        gap_report = gap["report"]
        gap_source = gap_report["source"]
        counts = gap_report["counts"]
        summary = gap["summary"]
        frames_path = Path(str(summary.get("provenance", {}).get("frames_path", "")))
        timeline_path = Path(
            str(summary.get("provenance", {}).get("primary_timeline_path", ""))
        )
        if (
            not frames_path.is_file()
            or _sha256(frames_path)
            != str(summary.get("provenance", {}).get("frames_sha256", "")).upper()
            or not timeline_path.is_file()
            or _sha256(timeline_path)
            != str(
                summary.get("provenance", {}).get("primary_timeline_sha256", "")
            ).upper()
        ):
            raise ValueError("scoring summary Pose/timeline source binding failed")
        ranges = [
            (int(item["start_ms"]), int(item["end_ms"]))
            for item in gap_report["event_failures"]
        ]
        targets, preflight = select_small_roi_targets(
            frame_rows=_load_jsonl(frames_path),
            timeline_rows=_load_jsonl(timeline_path),
            event_ranges=ranges,
            baseline_min_roi_size_px=32,
            experimental_min_roi_size_px=8,
            roi_margin=0.15,
            baseline_max_players=2,
        )
        preflight_total.update(preflight)
        experiment = experiments.get(video_id)
        vector_gain = operational_gain = 0
        experiment_binding: dict[str, Any] | None = None
        if experiment is None:
            if targets:
                raise ValueError("eligible small ROI targets require an experiment report")
        else:
            report = experiment["report"]
            if (
                str(report["source"]["gap_audit"]["path"])
                != str(gap["report_path"])
                or str(report["source"]["gap_audit"]["sha256"]).upper()
                != _sha256(gap["report_path"])
            ):
                raise ValueError("experiment does not bind its gap audit")
            attempted = [int(item) for item in report["inference"]["target_processed_indices"]]
            expected_targets = sorted(int(item["processed_index"]) for item in targets)
            if attempted != expected_targets:
                raise ValueError("experiment target frames differ from source preflight")
            vector_gain = int(
                report["feature_vector_impact"]["recovered_indicator_instance_count"]
            )
            operational_gain = int(
                report["operational_measurement_impact"][
                    "recovered_indicator_instance_count"
                ]
            )
            for identity in report["operational_measurement_impact"][
                "recovered_indicator_instances"
            ]:
                indicator_recoveries[str(identity["indicator_id"])] += 1
            experiment_binding = _source(experiment["report_path"])
            experiment_sources.append(
                {"video_id": video_id, "report": experiment_binding}
            )

        baseline["indicator_instances"] += int(counts["indicator_record_count"])
        baseline["operational_measured"] += int(
            counts["feature_measured_indicator_count"]
        )
        baseline["operational_unavailable"] += int(
            counts["feature_unavailable_indicator_count"]
        )
        baseline["vector_complete"] += int(
            counts["feature_vector_complete_indicator_count"]
        )
        baseline["vector_incomplete"] += int(
            counts["feature_vector_incomplete_indicator_count"]
        )
        baseline["measurement_hard_fail"] += int(
            counts["measurement_hard_fail_indicator_count"]
        )
        projected_measured = int(counts["feature_measured_indicator_count"]) + operational_gain
        projected_unavailable = int(counts["feature_unavailable_indicator_count"]) - operational_gain
        projected_vector_complete = int(
            counts["feature_vector_complete_indicator_count"]
        ) + vector_gain
        projected_vector_incomplete = int(
            counts["feature_vector_incomplete_indicator_count"]
        ) - vector_gain
        projected["operational_measured"] += projected_measured
        projected["operational_unavailable"] += projected_unavailable
        projected["vector_complete"] += projected_vector_complete
        projected["vector_incomplete"] += projected_vector_incomplete
        projected["measurement_hard_fail"] += int(
            counts["measurement_hard_fail_indicator_count"]
        )
        video_rows.append(
            {
                "video_id": video_id,
                "frame_count": int(
                    summary["provenance"]["frame_window"]["frame_count"]
                ),
                "baseline": {
                    "indicator_instances": int(counts["indicator_record_count"]),
                    "operational_measured": int(
                        counts["feature_measured_indicator_count"]
                    ),
                    "operational_unavailable": int(
                        counts["feature_unavailable_indicator_count"]
                    ),
                    "feature_vector_complete": int(
                        counts["feature_vector_complete_indicator_count"]
                    ),
                    "feature_vector_incomplete": int(
                        counts["feature_vector_incomplete_indicator_count"]
                    ),
                    "measurement_hard_fail": int(
                        counts["measurement_hard_fail_indicator_count"]
                    ),
                },
                "preflight": {
                    "eligible_small_roi_target_frames": len(targets),
                    "counts": dict(sorted(preflight.items())),
                },
                "experiment_report": experiment_binding,
                "experimental_projection": {
                    "feature_vector_recovered": vector_gain,
                    "operational_measurement_recovered": operational_gain,
                    "operational_measured": projected_measured,
                    "operational_unavailable": projected_unavailable,
                    "feature_vector_complete": projected_vector_complete,
                    "feature_vector_incomplete": projected_vector_incomplete,
                    "measurement_hard_fail_unchanged": int(
                        counts["measurement_hard_fail_indicator_count"]
                    ),
                },
            }
        )
        gap_sources.append(
            {"video_id": video_id, "report": _source(gap["report_path"])}
        )

    operational_gain = projected["operational_measured"] - baseline[
        "operational_measured"
    ]
    vector_gain = projected["vector_complete"] - baseline["vector_complete"]
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "recovery_id": recovery_id,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "status": "experimental_observability_gain_requires_keypoint_truth_and_release_protocol",
        "scope": {
            "video_count": len(gaps),
            "indicator_count": 13,
            "current_profile": current_profile,
            "registry_version": registry_version,
            "registry_sha256": registry_sha,
        },
        "sources": {
            "gap_audits": gap_sources,
            "small_roi_experiments": experiment_sources,
        },
        "baseline": {
            "indicator_instances": baseline["indicator_instances"],
            "operational_measured": baseline["operational_measured"],
            "operational_unavailable": baseline["operational_unavailable"],
            "feature_vector_complete": baseline["vector_complete"],
            "feature_vector_incomplete": baseline["vector_incomplete"],
            "measurement_hard_fail": baseline["measurement_hard_fail"],
        },
        "experiment": {
            "eligible_target_frames": int(
                preflight_total["targeted_baseline_size_guard_skip"]
            ),
            "pose_output_recovered_frames": sum(
                int(item["report"]["inference"]["counts"]["pose_output_recovered"])
                for item in experiments.values()
            ),
            "feature_vector_recovered": vector_gain,
            "operational_measurement_recovered": operational_gain,
            "regressed_feature_vectors": 0,
            "regressed_operational_measurements": 0,
        },
        "experimental_projection": {
            "operational_measured": projected["operational_measured"],
            "operational_unavailable": projected["operational_unavailable"],
            "feature_vector_complete": projected["vector_complete"],
            "feature_vector_incomplete": projected["vector_incomplete"],
            "measurement_hard_fail": projected["measurement_hard_fail"],
            "non_hard_fail_feature_incomplete": projected[
                "operational_unavailable"
            ]
            - projected["measurement_hard_fail"],
        },
        "indicator_operational_recovery_counts": dict(
            sorted(indicator_recoveries.items())
        ),
        "videos": video_rows,
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "automatic_fallback_enabled": False,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "experimental_projection_is_not_current_production_status": True,
            "keypoint_error_truth_and_independent_release_review_required": True,
        },
    }
    validate_multivideo_measurement_recovery_report(report)
    return report


def validate_multivideo_measurement_recovery_report(
    report: Mapping[str, Any]
) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get(
        "report_version"
    ) != REPORT_VERSION:
        raise ValueError("unsupported multivideo measurement recovery report")
    if report.get("status") != (
        "experimental_observability_gain_requires_keypoint_truth_and_release_protocol"
    ):
        raise ValueError("multivideo measurement recovery status is unsafe")
    safety = report.get("safety", {})
    prohibited = (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    )
    if any(safety.get(field) is not False for field in prohibited):
        raise ValueError("multivideo recovery contains an unsafe claim")
    if any(
        safety.get(field) is not True
        for field in (
            "experimental_projection_is_not_current_production_status",
            "keypoint_error_truth_and_independent_release_review_required",
        )
    ):
        raise ValueError("multivideo recovery lacks a required safety assertion")
    baseline = report.get("baseline", {})
    projection = report.get("experimental_projection", {})
    experiment = report.get("experiment", {})
    total = int(baseline.get("indicator_instances", -1))
    if total != int(baseline.get("operational_measured", -1)) + int(
        baseline.get("operational_unavailable", -1)
    ) or total != int(baseline.get("feature_vector_complete", -1)) + int(
        baseline.get("feature_vector_incomplete", -1)
    ):
        raise ValueError("baseline measurement counts do not balance")
    if total != int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) or total != int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ):
        raise ValueError("experimental projection counts do not balance")
    if int(projection.get("measurement_hard_fail", -1)) != int(
        baseline.get("measurement_hard_fail", -1)
    ):
        raise ValueError("small ROI experiment cannot change measurement hard fails")
    if int(experiment.get("operational_measurement_recovered", -1)) != int(
        projection.get("operational_measured", -1)
    ) - int(baseline.get("operational_measured", -1)):
        raise ValueError("operational recovery count is inconsistent")
    if int(experiment.get("feature_vector_recovered", -1)) != int(
        projection.get("feature_vector_complete", -1)
    ) - int(baseline.get("feature_vector_complete", -1)):
        raise ValueError("feature-vector recovery count is inconsistent")
    if int(projection.get("non_hard_fail_feature_incomplete", -1)) != int(
        projection.get("operational_unavailable", -1)
    ) - int(projection.get("measurement_hard_fail", -1)):
        raise ValueError("remaining unavailable decomposition is inconsistent")
    if any(
        int(experiment.get(field, -1)) != 0
        for field in (
            "regressed_feature_vectors",
            "regressed_operational_measurements",
        )
    ):
        raise ValueError("multivideo recovery cannot contain regressions")
    videos = report.get("videos")
    if not isinstance(videos, list) or len(videos) != int(
        report.get("scope", {}).get("video_count", -1)
    ):
        raise ValueError("multivideo recovery video scope is inconsistent")


def render_multivideo_measurement_recovery_html(
    report: Mapping[str, Any], *, output_path: Path
) -> str:
    validate_multivideo_measurement_recovery_report(report)
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(str(item['video_id']))}</code></td>"
        f"<td>{int(item['baseline']['operational_measured'])} / {int(item['baseline']['operational_unavailable'])}</td>"
        f"<td>{int(item['preflight']['eligible_small_roi_target_frames'])}</td>"
        f"<td>{int(item['experimental_projection']['feature_vector_recovered'])}</td>"
        f"<td>{int(item['experimental_projection']['operational_measurement_recovered'])}</td>"
        f"<td>{int(item['experimental_projection']['operational_measured'])} / {int(item['experimental_projection']['operational_unavailable'])}</td>"
        "</tr>"
        for item in report["videos"]
    )
    baseline = report["baseline"]
    projection = report["experimental_projection"]
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>RallyMate M66 测量恢复实验</title><style>body{{font-family:system-ui,sans-serif;max-width:1200px;margin:auto;padding:24px;background:#0f172a;color:#e2e8f0}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #475569;padding:8px}}.warn{{padding:12px;background:#3f2a08;border:1px solid #a16207}}code{{color:#93c5fd}}</style></head><body><h1>M66 三视频 Pose 测量恢复实验</h1><p class=\"warn\">这是同模型、同权重、固定候选边界的小 ROI 可观测性实验，不是准确率，不是生产 fallback，也不会生成 A～E。</p><p>当前生产测量：{int(baseline['operational_measured'])}/{int(baseline['indicator_instances'])}；实验投影：{int(projection['operational_measured'])}/{int(baseline['indicator_instances'])}。仍有 {int(projection['measurement_hard_fail'])} 条 measurement hard fail 和 {int(projection['non_hard_fail_feature_incomplete'])} 条非 hard-fail 特征向量不完整。</p><table><thead><tr><th>视频</th><th>当前 measured / unavailable</th><th>目标帧</th><th>向量恢复</th><th>门禁后恢复</th><th>实验投影 measured / unavailable</th></tr></thead><tbody>{rows}</tbody></table><p>生产默认未改变；正式启用前需要小 ROI 人工关键点误差、分视角结果和独立发布审核。</p></body></html>"""


__all__ = [
    "REPORT_VERSION",
    "SCHEMA_VERSION",
    "build_multivideo_measurement_recovery_report",
    "render_multivideo_measurement_recovery_html",
    "validate_multivideo_measurement_recovery_report",
]
