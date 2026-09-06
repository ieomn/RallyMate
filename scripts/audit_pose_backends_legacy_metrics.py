from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from rallymate_scoring.granularity import analyze_scoring_readiness


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)
BACKENDS = (
    "yolo",
    "rtmpose-s-halpe26-256x192",
    "rtmpose-m-halpe26-256x192",
    "rtmpose-m-halpe26-384x288",
)
TARGETS = {"FS01-M02", "FS01-M05", "FS02-M02", "FS09-M03", "FS09-M04", "FS09-M05"}


def _frames(root: Path, backend: str, video_id: str) -> Path:
    if backend == "yolo":
        return root / "runs" / "full-test" / video_id / "frames.jsonl"
    return root / "runs" / "pose-ab" / backend / video_id / "frames.jsonl"


def main() -> None:
    root = Path.cwd()
    models = []
    for backend in BACKENDS:
        videos = []
        for video_id in VIDEO_IDS:
            source_summary = json.loads(
                (root / "runs" / "full-test" / video_id / "summary.json").read_text(encoding="utf-8")
            )
            summary = copy.deepcopy(source_summary)
            if backend != "yolo":
                pose_summary = json.loads(
                    (root / "runs" / "pose-ab" / backend / video_id / "summary.json").read_text(encoding="utf-8")
                )
                summary["coverage"]["pose_frame_fraction"] = pose_summary["coverage"]["pose_frame_fraction"]
                summary["models"]["pose_backend"] = pose_summary["models"]["pose_backend"]
                summary["models"]["pose_format"] = "halpe26"
            audit = analyze_scoring_readiness(summary, _frames(root, backend, video_id))
            targets = [item for item in audit["indicator_results"] if item["indicator_id"] in TARGETS]
            videos.append(
                {
                    "video_id": video_id,
                    "summary": audit["summary"],
                    "observed_model_evidence": audit["observed_model_evidence"],
                    "pose_assessment": audit["pose_assessment"],
                    "six_target_indicators": targets,
                }
            )
        models.append(
            {
                "backend": backend,
                "videos": videos,
                "score_ready_all_videos": sum(item["summary"]["score_ready"] for item in videos),
                "score_blocked_all_videos": sum(item["summary"]["score_blocked"] for item in videos),
                "observed_evidence": {
                    status: sum(item["summary"]["observed_evidence"].get(status, 0) for item in videos)
                    for status in ("ready", "partial", "blocked")
                },
                "semantics": "legacy_298_static_granularity_audit_not_A_to_E_scoring",
            }
        )
    report = {
        "schema_version": "1.0.0",
        "report_version": "legacy-298-pose-backend-audit-2026-08-13.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "all_backends_score_ready_zero_event_feature_calibration_still_required",
        "protocol": {
            "videos": list(VIDEO_IDS),
            "same_frozen_detect_track_ball_racket_court": True,
            "pose_backend_varied": True,
            "indicator_count_per_video": 298,
        },
        "models": models,
        "conclusion": {
            "pose_granularity_can_improve_observed_evidence": True,
            "pose_replacement_alone_produces_valid_A_to_E": False,
            "remaining_global_blockers": [
                "event_segmentation_and_manual_event_truth",
                "feature_error_evaluation",
                "coach_calibration",
                "independent_test",
            ],
        },
    }
    output = root / "reports" / "pose-scoring-ab" / "legacy-298-audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "models": len(models), "status": report["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
