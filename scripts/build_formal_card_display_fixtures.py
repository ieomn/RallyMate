"""Recompute display fixtures; these do not claim new original-video decoding."""
from pathlib import Path
import hashlib
import json
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from rallymate_features.schemas import PoseSequence
from rallymate_features.source_alignment import clear_source_aligned_feature_cache
from rallymate_service.card_execution import build_card_execution_from_sequence
from rallymate_service.footwork_cycle_execution import compute_footwork_cycle_execution
from tests.test_footwork_cycle_card_evidence import synthetic_cycle
from tests.test_card_formal_scoring_inputs import gs_report, model_records
from tests.test_fs_step_motion_windows import fixture as step_fixture
from tests.test_gs_preload_evidence import fixture as preload_fixture, compute as compute_preload


def write(name, value):
    (ROOT / "scoring-demo-web/tests/fixtures" / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    source = ROOT / "tests/fixtures/temporal-card-real-0a3.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    data = payload["sequence"]
    for key in ("timestamp_ms", "source_frames", "frame_dimensions_px", "frame_transforms_to_reference"):
        data[key] = np.asarray(data[key])
    for key in ("keypoints_xy", "confidence", "model_estimate_masks"):
        data[key] = {name: np.asarray(value, dtype=bool if key == "model_estimate_masks" else float)
                     for name, value in data[key].items()}
    for key in ("camera_motion_status", "camera_reference_epochs", "model_estimate_bindings"):
        data[key] = tuple(data[key])
    data["source_time_bindings"] = {int(k): v for k, v in data["source_time_bindings"].items()}
    report = build_card_execution_from_sequence(PoseSequence(**data), payload["events"], payload["timeline"],
        source_run_id=payload["events"][0]["video_id"])
    report["cards"] = [card for card in report["cards"] if card["instances"]]
    write("temporal-formal-real-0a3-contract.json", {
        "evidence_kind": "real_recorded_trajectory_replay_not_new_original_video_IO_validation",
        "source_fixture": source.relative_to(ROOT).as_posix(),
        "source_fixture_utf8_lf_sha256": hashlib.sha256(source.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest(),
        "report": report})
    cases = []
    for code, cross, variant in [("FS04", False, "complete"), ("FS03", True, "complete"),
                                 ("FS04", False, "advisory"), ("FS04", False, "candidate_identity"),
                                 ("FS04", False, "confirmed_switch")]:
        clear_source_aligned_feature_cache()
        sequence, event, timeline, binding = synthetic_cycle(code, cross)
        if variant == "advisory":
            event["quality_flags"] = ["pose_kinematic_coverage_low", "keypoint_jump_candidates_present"]
            event["track_diagnostics"]["keypoint_jump_candidate_joints"] = ["left_ankle"]
        if variant == "candidate_identity":
            event["track_diagnostics"].update(primary_identity_ambiguous=True, source_track_switch_candidate_count=2)
            event["quality_flags"] = ["primary_identity_ambiguous", "source_track_switch_candidates_present"]
            for row in timeline:
                row["identity_ambiguous"] = True
        if variant == "confirmed_switch":
            event["track_diagnostics"]["confirmed_id_switch_count"] = 1
        result = compute_footwork_cycle_execution(sequence, [event], timeline, [binding], source_run_id=event["video_id"])
        result["version"] = "card-execution-v1.0.0"  # Test-only envelope, not an API call.
        result["cards"] = [card for card in result["cards"] if card["instances"]]
        cases.append({"event_code": code, "variant": variant, "report": result})
    write("footwork-cycle-formal-synthetic-contract.json", {
        "evidence_kind": "synthetic_known_trajectories_not_real_steps",
        "envelope": "test_only_unified_envelope_not_production_API_or_real_video_validation",
        "real_step_motion_instances": 0, "cases": cases})
    stages = []
    for variant in ("detector", "primary_epoch_zero", "model_epoch_zero"):
        arguments = step_fixture("FS08-M02")
        if variant == "model_epoch_zero":
            model_records(arguments)
            for record in arguments[0]:
                record["poses"][0]["source_binding"]["selection_epoch"] = 0
        if variant != "detector":
            for row in arguments[1]:
                row["selection_epoch"] = 0
        result = gs_report(arguments)
        result["version"] = "card-execution-v1.0.0"
        result["cards"] = [card for card in result["cards"] if card["instances"]]
        stages.append({"variant": variant, "report": result})
    result = compute_preload(preload_fixture())
    result["version"] = "card-execution-v1.0.0"
    result["cards"] = [card for card in result["cards"] if any("auxiliary_evidence" in i for i in card["instances"])]
    stages.append({"variant": "manual_preload_auxiliary", "report": result})
    write("gs-formal-synthetic-contract.json", {"evidence_kind": "synthetic_source_contract_not_real_tennis_stages",
        "real_stage_instances": 0, "envelope": "test_only_unified_envelope_not_production_API_or_real_video_validation", "cases": stages})
    print("Recomputed one recorded-trajectory, five synthetic cycle and four synthetic GS display reports")


if __name__ == "__main__":
    main()
