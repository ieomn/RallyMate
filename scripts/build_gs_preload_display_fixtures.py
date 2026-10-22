"""Recompute synthetic auxiliary reader fixtures; no real M01 evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.test_gs_preload_evidence import fixture, compute


def excerpt(report):
    report["version"] = "card-execution-v1.0.0"
    report["cards"] = [c for c in report["cards"] if any("auxiliary_evidence" in i for i in c["instances"])]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.example.read_text(encoding="utf-8"))
    data["upstream_sha256"] = hashlib.sha256(args.example.read_bytes()).hexdigest()
    data["upstream_service_version"] = data["report"]["version"]
    data["envelope"] = "test_only_unified_envelope_not_production_API_or_real_video_validation"
    data["report"] = excerpt(data["report"])
    data["additional_synthetic_cases"] = []
    for label in ("GS02", "GS03", "GS04", "GS05", "six_frames", "joint_gap", "parent_stage_gap", "nonzero_pts_duration", "between_frame_parent_bounds"):
        case = fixture(code=label if label.startswith("GS") else "GS01", count=6 if label == "six_frames" else 15 if label in {"parent_stage_gap", "between_frame_parent_bounds"} else 12)
        if label == "between_frame_parent_bounds":
            stage = case["annotations"]["stages"][0]
            stage.update(start_ms=15, end_ms=545)
            for key, index in (("start_frame", 1), ("end_frame", 13)):
                stage["local_preload_observation"][key] = {field: case["decoder_timeline"][index][field]
                    for field in ("frame_index", "pts", "time_base_num", "time_base_den")}
        if label == "joint_gap":
            next(p for p in case["frame_records"][5]["poses"][0]["keypoints"] if p["name"] == "left_ankle")["confidence"] = .1
        if label == "parent_stage_gap":
            stage = case["annotations"]["stages"][0]
            stage["local_preload_observation"]["end_frame"] = {k: case["decoder_timeline"][9][k] for k in ("frame_index", "pts", "time_base_num", "time_base_den")}
            case["frame_records"].pop(13); case["primary_timeline"].pop(13)
        if label == "nonzero_pts_duration":
            times = [9000 + i * 20 if i <= 5 else 9100 + (i - 5) * 40 for i in range(12)]
            for i, time in enumerate(times):
                case["frame_records"][i]["frame"]["timestamp_ms"] = time
                case["primary_timeline"][i]["timestamp_ms"] = time
                case["decoder_timeline"][i]["pts"] = time
                for point in case["frame_records"][i]["poses"][0]["keypoints"]:
                    if point["name"] in ("left_hip", "right_hip"):
                        point["y_px"] = 110 + min(i, 5)
            stage = case["annotations"]["stages"][0]
            for row in (stage, case["annotations"]["actions"][0]):
                row.update(start_ms=times[0], end_ms=times[-1])
            stage["local_preload_observation"]["start_frame"]["pts"] = times[0]
            stage["local_preload_observation"]["end_frame"]["pts"] = times[-1]
        data["additional_synthetic_cases"].append({"label": label, "report": excerpt(compute(case))})
    output = ROOT / "scoring-demo-web/tests/fixtures/gs-preload-synthetic-contract.json"
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Saved ten synthetic auxiliary contract cases; real M01 instances: 0")


if __name__ == "__main__":
    main()
