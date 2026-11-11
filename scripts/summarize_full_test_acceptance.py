"""Join source-bound scoring, UI parsing and portable-export acceptance receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_full_test_scoring import now, sha256, write_json


def summarize(directory):
    def read(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    inventory = read("video-inventory.json")
    backend, frontend = read("validation-results.json"), read("frontend-validation.json")
    sources = read("scorer-source-sha256.json")
    source_current = all((ROOT / name).is_file() and sha256(ROOT / name) == value for name, value in sources.items())
    rows = []
    for video in inventory:
        result = next((row for row in backend["results"] if row["name"] == video["name"]), {})
        ui = next((row for row in frontend["results"] if row["name"] == video["name"]), {})
        stem = Path(video["name"]).stem
        export = read(f"export-validation/{stem}.json")
        bound_export = export.get("source_run_id") == result.get("job_id") and export.get("source_video_sha256") == video["sha256"]
        valid = (result.get("valid_numeric_score") is True and result.get("required_features_valid") is True
                 and ui.get("passed") is True and export.get("passed") is True and bound_export)
        rows.append({"name": video["name"], "source_sha256": video["sha256"], "job_id": result.get("job_id"),
                     "inference_reused": result.get("inference_reused"), "source_frames": video["frames"],
                     "native_frames": result.get("evidence", {}).get("native_frame_count"),
                     "selected_native_frames": result.get("evidence", {}).get("selected_native_frame_count"),
                     "score": result.get("score"), "episodes": result.get("observation_count"),
                     "phase_anchors": result.get("measured_phase_anchor_count"),
                     "joint_metric_kinds": len(result.get("measured_joint_angle_episode_counts", {})),
                     "level_assessments": result.get("level_assessment_count"), "training_clips": export.get("training_clips"),
                     "ui_and_export_passed": bool(ui.get("passed") and export.get("passed") and bound_export),
                     "assessment_sha256": sha256(directory / "assessments" / f"{stem}.json"), "passed": valid})
    validators = ["scripts/validate_full_test_scoring.py", "scripts/rebuild_subject_selection.py",
                  "scripts/summarize_full_test_acceptance.py", "scoring-demo-web/scripts/validate-full-test-scoring.mjs",
                  "scoring-demo-web/scripts/validate-training-session-report.mjs"]
    result = {"evaluated_at": now(), "source_code_current": source_current,
              "scorer_source_fingerprint": backend.get("scorer_source_fingerprint"),
              "validator_sha256": {name: sha256(ROOT / name) for name in validators},
              "videos": rows, "video_count": len(rows), "source_frame_count": sum(row["source_frames"] for row in rows),
              "episode_count": sum(row["episodes"] or 0 for row in rows),
              "measured_phase_anchor_count": sum(row["phase_anchors"] or 0 for row in rows),
              "fresh_inference_count": sum(row["inference_reused"] is False for row in rows),
              "exact_source_cached_inference_count": sum(row["inference_reused"] is True for row in rows),
              "all_passed": source_current and backend.get("all_passed") is True and frontend.get("all_passed") is True
                  and len(rows) > 0 and all(row["passed"] for row in rows)}
    write_json(directory / "acceptance-summary.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/scoring-refactor-20261108")
    receipt = summarize(parser.parse_args().output)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    raise SystemExit(0 if receipt["all_passed"] else 1)
