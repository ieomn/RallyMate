from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from rallymate_service.config import load_settings
from rallymate_service.database import JobDatabase
from rallymate_service.worker import PersistentVisionRunner, process_one
from rallymate_scoring.feasibility import (
    feasibility_event_codes,
    load_feasibility_registry,
)
from rallymate_vision.contracts import load_request
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.pose.presets import resolve_pose_deployment_preset
from rallymate_vision.validation import validate_run_artifacts


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _registry_scope(registry: dict) -> tuple[set[str], set[str]]:
    indicator_ids = {
        str(indicator["indicator_id"]) for indicator in registry["indicators"]
    }
    event_codes = feasibility_event_codes(registry)
    declared_count = registry["scope"].get("indicator_count")
    if declared_count != len(indicator_ids):
        raise RuntimeError(
            "registry scope indicator_count mismatch: "
            f"declared={declared_count}, actual={len(indicator_ids)}"
        )
    return indicator_ids, event_codes


def _validate_scoring_contract(
    *,
    registry: dict,
    events: list[dict],
    indicators: list[dict],
    scores: list[dict],
    loop_summary: dict,
) -> dict:
    expected_indicators, required_events = _registry_scope(registry)
    event_counts = Counter(item["event_code"] for item in events)
    missing_events = required_events - set(event_counts)
    if missing_events:
        raise RuntimeError(f"missing event candidates: {sorted(missing_events)}")
    if not indicators or not scores:
        raise RuntimeError("registry-derived indicator scoring artifacts are empty")

    observed_indicators = {item["indicator_id"] for item in indicators}
    observed_score_indicators = {item["indicator_id"] for item in scores}
    if observed_indicators != expected_indicators:
        raise RuntimeError(
            "indicator contract mismatch: "
            f"expected={sorted(expected_indicators)}, "
            f"observed={sorted(observed_indicators)}"
        )
    if observed_score_indicators != expected_indicators:
        raise RuntimeError(
            "score indicator contract mismatch: "
            f"expected={sorted(expected_indicators)}, "
            f"observed={sorted(observed_score_indicators)}"
        )

    non_null_grades = sum(item.get("grade") is not None for item in scores)
    non_null_threshold_versions = sum(
        item.get("threshold_version") is not None for item in scores
    )
    if non_null_grades:
        raise RuntimeError("a grade was emitted without coach calibration")
    if non_null_threshold_versions:
        raise RuntimeError("a threshold version was emitted without coach calibration")

    status_counts = Counter(item["status"] for item in scores)
    if set(status_counts) - {"calibration_required", "unavailable"}:
        raise RuntimeError(
            "uncalibrated deployment smoke emitted an unsafe score status: "
            f"{sorted(status_counts)}"
        )
    if status_counts.get("calibration_required", 0) == 0:
        raise RuntimeError("no calibration_required score was emitted")

    target_count = loop_summary["result_state"]["target_indicator_count"]
    if target_count != len(expected_indicators):
        raise RuntimeError(
            "scoring-loop target indicator count disagrees with registry: "
            f"summary={target_count}, registry={len(expected_indicators)}"
        )
    if loop_summary["model_versions"]["feasibility_registry"] != registry["registry_version"]:
        raise RuntimeError("scoring-loop summary lost feasibility registry version")
    if loop_summary["safety_assertions"].get(
        "any_non_null_grade_without_calibration"
    ):
        raise RuntimeError("scoring-loop summary reports an unsafe uncalibrated grade")
    if loop_summary["safety_assertions"].get("fake_thresholds_generated"):
        raise RuntimeError("scoring-loop summary reports generated fake thresholds")

    return {
        "required_event_codes": sorted(required_events),
        "event_counts": dict(event_counts),
        "indicator_ids": sorted(observed_indicators),
        "score_status_counts": dict(status_counts),
        "non_null_grade_count": non_null_grades,
        "non_null_threshold_version_count": non_null_threshold_versions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exercise an RTMPose deployment preset through the persistent Worker path"
    )
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument(
        "--preset",
        help="explicit preset override; omit to exercise the service registry default",
    )
    parser.add_argument("--registry", required=True, type=Path)
    args = parser.parse_args()

    registry_path = args.registry.resolve()
    if args.preset:
        os.environ["RALLYMATE_POSE_PRESET"] = args.preset
    os.environ["RALLYMATE_SCORING_FEASIBILITY_REGISTRY"] = str(registry_path)
    os.environ.setdefault("RALLYMATE_MODEL_LICENSE_ACK", "alternative-backend")
    settings = load_settings()
    request = load_request(args.request.resolve())
    resolved_preset_id = settings.pose_preset
    if not resolved_preset_id:
        raise RuntimeError("service did not resolve a pose deployment preset")
    preset = resolve_pose_deployment_preset(resolved_preset_id)
    if settings.pose_preset != request.models.pose_preset:
        raise RuntimeError(
            f"service preset {settings.pose_preset!r} does not match request "
            f"preset {request.models.pose_preset!r}"
        )
    if settings.resolved_scoring_feasibility_registry != registry_path:
        raise RuntimeError("service settings did not resolve the explicit registry")
    if request.scoring.feasibility_registry is None:
        raise RuntimeError("deployment smoke request must explicitly declare its registry")
    if request.scoring.feasibility_registry.resolve() != registry_path:
        raise RuntimeError(
            "request/service registry mismatch: "
            f"request={request.scoring.feasibility_registry.resolve()}, "
            f"service={registry_path}"
        )
    registry = load_feasibility_registry(registry_path)

    with tempfile.TemporaryDirectory(prefix="rallymate-pose-worker-smoke-") as directory:
        database = JobDatabase(Path(directory) / "jobs.sqlite3")
        database.initialize()
        database.create_job(
            request.job_id,
            request.video_path.name,
            request.video_path,
            args.request.resolve(),
            request.output_dir,
        )
        runner = PersistentVisionRunner(settings)
        if not process_one(settings, database, "pose-deployment-smoke", runner=runner):
            raise RuntimeError("persistent Worker did not claim the smoke job")
        job = database.get_job(request.job_id)
        if job is None or job["status"] != "succeeded":
            raise RuntimeError(json.dumps(job, ensure_ascii=False, indent=2))

    output_dir = request.output_dir
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    events = _jsonl(output_dir / "events.jsonl")
    indicators = _jsonl(output_dir / "indicator-features.jsonl")
    scores = _jsonl(output_dir / "scores.jsonl")
    loop_summary = json.loads(
        (output_dir / "scoring-loop-summary.json").read_text(encoding="utf-8")
    )
    contract = _validate_scoring_contract(
        registry=registry,
        events=events,
        indicators=indicators,
        scores=scores,
        loop_summary=loop_summary,
    )
    bundle_validation = validate_run_artifacts(output_dir)
    supplemental = [
        feature
        for indicator in indicators
        for feature in indicator.get("supplemental_features", [])
    ]
    supplemental_valid = Counter(
        feature["feature_name"] for feature in supplemental if feature["valid"]
    )
    supplemental_total = Counter(feature["feature_name"] for feature in supplemental)

    pose_backend = summary["models"]["pose_backend"]
    if pose_backend["native_keypoint_format"] != preset.native_keypoint_format:
        raise RuntimeError(
            "deployment smoke native keypoint format disagrees with preset: "
            f"expected={preset.native_keypoint_format}, "
            f"observed={pose_backend['native_keypoint_format']}"
        )
    if (
        request.models.pose_native_keypoint_format is not None
        and request.models.pose_native_keypoint_format != preset.native_keypoint_format
    ):
        raise RuntimeError("request native keypoint format disagrees with preset")
    if summary["models"]["pose_deployment_preset"] != resolved_preset_id:
        raise RuntimeError("summary lost deployment preset provenance")
    registry_sha256 = sha256_file(registry_path)
    provenance = loop_summary["provenance"]
    if Path(provenance["feasibility_registry_path"]).resolve() != registry_path:
        raise RuntimeError("scoring-loop provenance lost registry path")
    if provenance["feasibility_registry_sha256"].upper() != registry_sha256.upper():
        raise RuntimeError("scoring-loop provenance registry SHA-256 mismatch")

    report = {
        "schema_version": "1.1.0",
        "status": "passed",
        "execution_path": "JobDatabase -> PersistentVisionRunner -> process_one -> run_pipeline",
        "semantics": {
            "purpose": "deployment_path_and_registry_contract_smoke",
            "accuracy_claim": False,
            "event_accuracy_evaluated": False,
            "feature_accuracy_evaluated": False,
            "grade_accuracy_evaluated": False,
            "reason": "no_manual_event_keypoint_or_coach_ground_truth_in_deployment_smoke",
        },
        "preset": resolved_preset_id,
        "preset_source": (
            "explicit_cli_override" if args.preset else "deployment_registry_default"
        ),
        "preset_contract": preset.to_dict(),
        "job_id": request.job_id,
        "processed_frames": summary["processing"]["processed_frames"],
        "elapsed_seconds": summary["processing"]["elapsed_seconds"],
        "effective_processed_fps": summary["processing"]["effective_processed_fps"],
        "pose_backend": pose_backend,
        "feasibility_registry": {
            "path": str(registry_path),
            "sha256": registry_sha256,
            "registry_version": registry["registry_version"],
            "indicator_count": len(contract["indicator_ids"]),
            "indicator_ids": contract["indicator_ids"],
        },
        "required_event_codes": contract["required_event_codes"],
        "event_counts": contract["event_counts"],
        # Kept at the top level for readers of schema 1.0.0.
        "indicator_ids": contract["indicator_ids"],
        "score_status_counts": contract["score_status_counts"],
        "non_null_grade_count": contract["non_null_grade_count"],
        "non_null_threshold_version_count": contract[
            "non_null_threshold_version_count"
        ],
        "indicator_feature_validity": summary["minimum_scoring_loop"][
            "indicator_feature_validity"
        ],
        "supplemental_foot_feature_validity": {
            name: {
                "valid": supplemental_valid[name],
                "total": total,
                "valid_rate": round(supplemental_valid[name] / total, 6) if total else 0.0,
            }
            for name, total in sorted(supplemental_total.items())
        },
        "safety_assertions": {
            "required_event_codes_present": True,
            "registry_indicator_artifacts_non_empty": True,
            "actual_indicator_ids_match_registry": True,
            "score_indicator_ids_match_registry": True,
            "no_grade_without_calibration": True,
            "no_threshold_without_calibration": True,
            "fake_thresholds_generated": False,
            "accuracy_claim": False,
        },
        "bundle_validation": bundle_validation,
        "artifacts": summary["artifacts"],
    }
    report_path = output_dir / "deployment-smoke.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
