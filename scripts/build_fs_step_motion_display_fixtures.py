"""Recompute synthetic raw-source adapter reports; no real step is established."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.test_fs_step_motion_windows import fixture
from tests.test_fs_card_windows import compute
from rallymate_scoring.step_motion_evidence import SUPPORTED_CARDS
from rallymate_service.gs_card_execution import _prepare_samples
from rallymate_vision.pose.metadata import keypoint_schema


def as_bound_synthetic_model(args, selected_frames=None):
    """Exercise the formal SAM input contract with known synthetic 2D points."""
    samples, _ = _prepare_samples(args[0], args[1], args[3],
        video_sha256=args[-1]["video_sha256"], job_id=args[-1]["source_run_id"])
    bindings = {sample.frame_index: deepcopy(sample.source_binding) for group in samples.values() for sample in group}
    for record in args[0]:
        frame = record["frame"]
        if selected_frames is not None and frame["index"] not in selected_frames:
            continue
        points = {p["name"]: p for p in record["poses"][0]["keypoints"]}
        binding = bindings[frame["index"]]
        binding["processed_index"] = frame["processed_index"]
        keypoints = []
        for definition in keypoint_schema("mhr70")["keypoints"]:
            point = points.get(definition["name"])
            keypoints.append({**definition, "source": "sam3d", "observation_semantics": "model_estimate",
                "confidence_semantics": "not_provided_by_sam3d", "confidence": None, "observed": None, "visibility": None,
                "raw_x_px": point["x_px"] if point else None, "raw_y_px": point["y_px"] if point else None,
                "in_frame": point is not None})
        record["poses"] = [{"person_track_id": 1, "source": "sam3d", "keypoint_format": "mhr70",
            "observation_semantics": "monocular_model_estimate", "confidence_semantics": "not_provided_by_sam3d",
            "confidence": None, "status": "ok", "coordinate_space": "original_frame",
            "source_binding": binding, "keypoints": keypoints}]


def main():
    cases = []
    for identifier, variant in ([(card, "complete") for card in sorted(SUPPORTED_CARDS)]
            + [("FS08-M02", v) for v in ("small_displacement", "wrong_declared_side", "original_quality", "joint_gap", "missing_motion_observation",
                "two_motions", "short_middle", "endpoint_overlap", "out_and_back", "other_net_dominates", "competing_cycles", "phase_79ms", "phase_80ms")]
            + [("FS08-M03", "camera_rotation_compensated"), ("FS08-M02", "bound_synthetic_model")]
            + [("FS08-M02", name) for name in ("sam0_primary_derived1", "mixed_sam0_primary0", "sam1_primary0")]):
        args = fixture(identifier)
        window = args[-1]["windows"][0]
        first = window["start_source_frame"]
        if variant == "small_displacement":
            for record in args[0]:
                point = next(p for p in record["poses"][0]["keypoints"] if p["name"] == "right_ankle")
                point["y_px"] = 140 + (point["y_px"] - 140) * .4
        if variant == "wrong_declared_side": window["step_motion_observation"]["moving_foot"] = "left"
        if variant == "original_quality":
            args[2][0]["quality_flags"] = ["keypoint_jump_candidates_present"]
            args[2][0]["track_diagnostics"]["keypoint_jump_candidate_joints"] = ["right_ankle"]
        if variant == "joint_gap":
            record = next(r for r in args[0] if r["frame"]["index"] == first)
            next(p for p in record["poses"][0]["keypoints"] if p["name"] == "right_ankle")["confidence"] = .1
        if variant == "missing_motion_observation": del window["step_motion_observation"]
        paths = {
            "two_motions": [0]*4 + [.1, .2, .3, .3, .3, .5, .6, .7, .8, .9] + [.9]*4,
            "short_middle": [0]*8 + [1]*10,
            "endpoint_overlap": [0]*3 + [.1*i for i in range(1, 12)] + [1.1]*4,
            "out_and_back": [0]*4 + [.2, .4, .6, .8, 1, .8, .6, .4, .2, 0] + [0]*4,
        }
        if variant in paths or variant in {"other_net_dominates", "competing_cycles"}:
            arc = [(0,0)]*4 + [(0,.12),(0,.24),(0,.36),(.12,.4),(.24,.4),(.36,.4),(.4,.28),(.4,.16),(.4,.04),(.4,0)] + [(.4,0)]*4
            for record in args[0]:
                index = record["frame"]["index"]-first
                if not 0 <= index < 18: continue
                points = {p["name"]: p for p in record["poses"][0]["keypoints"]}
                if variant in paths: points["right_ankle"]["y_px"] = 140 + 50*paths[variant][index]
                if variant == "other_net_dominates":
                    points["right_ankle"]["x_px"], points["right_ankle"]["y_px"] = 225+50*arc[index][0], 140+50*arc[index][1]
                    points["left_ankle"]["y_px"] = 140+50*index/17
                if variant == "competing_cycles": points["left_ankle"]["y_px"] = 140+(points["right_ankle"]["y_px"]-140)*.8
        if variant in {"phase_79ms", "phase_80ms"}:
            duration = 79 if variant == "phase_79ms" else 80
            def time(index):
                return 20+40*index if index <= first else 180+[0,26,duration-27,duration][index-first] if index <= first+3 else 180+duration+40*(index-first-3)
            for record in args[0]: record["frame"]["timestamp_ms"] = time(record["frame"]["index"])
            for record in args[1]: record["timestamp_ms"] = time(record["source_frame_index"])
            for record in args[3]: record["pts"] = time(record["frame_index"])*90
            window.update(start_ms=time(first), end_ms=time(window["end_source_frame"]))
            args[2][0].update(start_ms=window["start_ms"], end_ms=window["end_ms"])
            for record in window["step_motion_observation"]["source_frames"]: record["pts"] = time(record["frame_index"])*90
        if variant == "camera_rotation_compensated":
            for record in args[0]:
                angle = max(0, record["frame"]["index"]-first)*math.pi/90
                c, s, cx, cy = math.cos(angle), math.sin(angle), 200, 90
                for p in record["poses"][0]["keypoints"]:
                    x, y = p["x_px"]-cx, p["y_px"]-cy
                    p["x_px"], p["y_px"] = cx+c*x-s*y, cy+s*x+c*y
                record["camera_motion"]["matrix_to_reference"] = [[c, s, cx-c*cx-s*cy], [-s, c, cy+s*cx-c*cy]]
        if variant == "bound_synthetic_model": as_bound_synthetic_model(args)
        if variant in {"sam0_primary_derived1", "mixed_sam0_primary0", "sam1_primary0"}:
            as_bound_synthetic_model(args, {first} if variant == "mixed_sam0_primary0" else None)
            for record in args[0]:
                for pose in record["poses"]:
                    if pose.get("source") == "sam3d": pose["source_binding"]["selection_epoch"] = 1 if variant == "sam1_primary0" else 0
            if variant != "sam0_primary_derived1":
                for row in args[1]: row["selection_epoch"] = 0
        report = compute(deepcopy(args))
        report["version"] = "card-execution-v1.0.0"
        report["cards"] = [row for row in report["cards"] if row["instances"]]
        cases.append({"indicator_id": identifier, "variant": variant, "report": report})
    modules = ["src/rallymate_scoring/step_motion_evidence.py", "src/rallymate_scoring/card_kinematics.py", "src/rallymate_scoring/card_evaluation.py",
        "src/rallymate_service/fs_card_windows.py", "src/rallymate_service/gs_card_execution.py",
        "src/rallymate_scoring/stroke_candidates.py", "src/rallymate_scoring/pose_observations.py",
        "src/rallymate_vision/pose/metadata.py", "tests/test_fs_step_motion_windows.py"]
    result = {"evidence_kind": "synthetic_known_trajectories_not_real_steps", "real_step_motion_instances": 0,
        "envelope": "test_only_unified_envelope_not_production_API_or_real_video_validation",
        "module_utf8_lf_sha256": {p: hashlib.sha256((ROOT / p).read_bytes().decode("utf-8").replace("\r\n", "\n").encode()).hexdigest() for p in modules},
        "cases": cases}
    path = ROOT / "scoring-demo-web/tests/fixtures/fs-step-motion-formal-synthetic-contract.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(cases)} synthetic adapter reports; real step-motion positives: 0")


if __name__ == "__main__":
    main()
