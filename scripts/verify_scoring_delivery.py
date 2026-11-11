"""Check the running web gateway delivers the same FULL-TEST scores as disk."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=Path("reports/scoring-refactor-20261108"))
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    source = json.loads((args.report_dir / "validation-results.json").read_text(encoding="utf-8"))
    if not source.get("all_passed"):
        raise ValueError("Run complete local FULL-TEST acceptance before delivery verification")
    results = []
    directory = args.report_dir / "delivered-assessments"
    directory.mkdir(exist_ok=True)
    for row in source["results"]:
        report = None
        started = perf_counter()
        try:
            with urlopen(args.base.rstrip("/") + "/v1/jobs/" + row["job_id"] + "/tennis-assessment", timeout=240) as response:
                report = json.load(response)
            (directory / (Path(row["name"]).stem + ".json")).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            expected = json.loads((args.report_dir / "assessments" / (Path(row["name"]).stem + ".json")).read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("Assessment response must be an object")
            fields = sorted(set(expected) | set(report))
            mismatches = [field for field in fields
                          if field not in report or field not in expected or report[field] != expected[field]]
            passed = report.get("source_video_sha256") == row["sha256"] and not mismatches
            result = {"name": row["name"], "passed": passed, "score": report.get("overall_score"), "mismatched_fields": mismatches}
        except Exception as error:
            result = {"name": row["name"], "passed": False, "error": f"{type(error).__name__}: {error}"}
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    output = {"evaluated_at": datetime.now(timezone.utc).isoformat(),
              "comparison": "complete_deterministic_assessment", "base_url": args.base,
              "all_passed": bool(results) and all(row["passed"] for row in results), "results": results}
    (args.report_dir / "api-delivery-validation.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if output["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
