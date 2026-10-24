"""Replay an explicit source-bound FS05 image-reference annotation document.

This creates an independent two-card report. It does not merge into the 298-card
service report and cannot verify contact, predicted impact or court calibration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_service.directional_micro_adjustment import build_directional_micro_adjustment_report


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.annotations.read_text(encoding="utf-8-sig"))
    report = build_directional_micro_adjustment_report(args.run_directory, document, video_path=args.video)
    args.output_directory.mkdir(parents=True, exist_ok=True)
    report_path = args.output_directory / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    modules = {}
    for name, module in sorted(sys.modules.items()):
        if not name.startswith("rallymate_") or not getattr(module, "__file__", None):
            continue
        path = Path(module.__file__).resolve()
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        modules[relative] = {"raw_file_sha256": hashlib.sha256(content).hexdigest(),
            "utf8_lf_sha256": hashlib.sha256(content.decode("utf-8").replace("\r\n", "\n").encode()).hexdigest()}
    evidence = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "report_sha256": sha(report_path), "annotation_raw_sha256": sha(args.annotations),
        "input": report["input"], "source_run_id": document["source_run_id"],
        "runtime_module_digests": modules, "runner_raw_sha256": sha(__file__),
        "scope": "independent_original_media_service_replay_not_unified_298_card_api",
        "original_grade_supported": False}
    (args.output_directory / "validation-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
