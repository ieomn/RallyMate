"""Build labelled frontend contract fixtures; never infer tennis labels or expert grades."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from rallymate_service.fs_card_windows import supported_rules
from tests.test_fs_card_windows import compute, fixture

OUTPUT = ROOT / "scoring-demo-web/tests/fixtures"


def fs_unit_fixture():
    cards = []
    for identifier in supported_rules():
        arguments = list(fixture(identifier))
        # Deliberately repeated poses and clean identity diagnostics isolate the
        # reader contract. These are synthetic unit inputs, not video results.
        pose = deepcopy(next(row["poses"][0] for row in arguments[0] if row["poses"] and row["frame"]["index"] == 10))
        for point in pose["keypoints"]:
            point["confidence"] = .9
        camera = deepcopy(arguments[0][10]["camera_motion"])
        for row in arguments[0]:
            row["poses"], row["camera_motion"] = [deepcopy(pose)], deepcopy(camera)
        arguments[1] = [{"processed_index": row["frame"]["processed_index"], "source_frame_index": row["frame"]["index"],
            "timestamp_ms": row["frame"]["timestamp_ms"], "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False} for row in arguments[0]]
        annotation = arguments[-1]["windows"][0]
        arguments[2] = [{"video_id": arguments[-1]["source_run_id"], "person_track_id": 1, "event_id": "unit-quality-context",
            "start_ms": annotation["start_ms"], "end_ms": annotation["end_ms"], "quality_flags": [],
            "track_diagnostics": {"source_track_ids": [1], "primary_identity_ambiguous": False, "source_track_switch_candidate_count": 0}}]
        report = compute(arguments)
        cards.append(next(row for row in report["cards"] if row["indicator_id"] == identifier))
    report.update(version="card-execution-v1.0.0", cards=cards)
    report.pop("summary", None)
    report.pop("coverage", None)
    report.pop("fs_card_windows", None)
    return {"semantics": "synthetic_repeated_poses_contract_tests_only_not_real_video_validation",
        "envelope": "test_only_composition_of_separate_card_windows_not_a_production_API_response", "report": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gs-reports", nargs="+", type=Path)
    parser.add_argument("--fs-report", type=Path)
    args = parser.parse_args()
    synthetic = fs_unit_fixture()
    (OUTPUT / "fs-card-window-contract-unit.json").write_text(json.dumps(synthetic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("FS unit dispositions:", dict(Counter(i["status"] for c in synthetic["report"]["cards"] for i in c["instances"])))
    if args.gs_reports:
        reports = []
        for path in args.gs_reports:
            data = json.loads(path.read_text(encoding="utf-8"))
            original_version = data["version"]
            data["cards"] = [row for row in data["cards"] if row["instances"]]
            data["version"] = "card-execution-v1.0.0"
            reports.append({"original_report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "original_service_version": original_version, "report": data})
        payload = {"provenance": {"kind": "actual_original_video_GS_artifact_outputs",
            "envelope": "test_only_unified_envelope_for_standalone_service_outputs_not_HTTP_validation",
            "warning": "sampled_racket_hands_not_dominant_hand_or_continuous_racket_hold;no_new_positive_wrist_speed"}, "reports": reports}
        (OUTPUT / "gs-racket-side-real.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("GS observed instances:", sum(len(c["instances"]) for r in reports for c in r["report"]["cards"]))
    if args.fs_report:
        data = json.loads(args.fs_report.read_text(encoding="utf-8"))
        assert data["version"] == "card-execution-v1.0.0"
        data["cards"] = [row for row in data["cards"] if row["indicator_id"] in supported_rules() and row["instances"]]
        payload = {"provenance": {"kind": "actual_original_video_unified_artifact_report_excerpt",
            "original_report_sha256": hashlib.sha256(args.fs_report.read_bytes()).hexdigest(),
            "warning": "manual_development_windows_not_expert_technique_completion_or_HTTP_validation;no_score_expected"}, "report": data}
        (OUTPUT / "fs-card-window-real.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("FS real dispositions:", dict(Counter(i["status"] for c in data["cards"] for i in c["instances"])))


if __name__ == "__main__":
    main()
