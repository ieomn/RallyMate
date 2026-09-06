from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.calibration_ranking import (
    RankingCalibrationError,
    evaluate_ranking_independent_test,
    load_jsonl,
    write_json,
)


def _load_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RankingCalibrationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RankingCalibrationError(f"{path} must contain a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a ranking-only candidate on its exact sealed independent test. "
            "Passing validates relative order only and can never approve A-E scoring."
        )
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise RankingCalibrationError(f"refusing to overwrite output: {args.output}")
        report = evaluate_ranking_independent_test(
            _load_object(args.candidate),
            _load_object(args.protocol),
            load_jsonl(args.samples),
            evaluated_at=args.evaluated_at,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
    except (OSError, RankingCalibrationError, ValueError) as exc:
        parser.exit(2, f"ranking independent test rejected: {exc}\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output.resolve()),
                "target": "relative_order_only",
                "A_E_grade_approved": False,
                "production_scoring_approved": False,
                "A_E_scoring_ready": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
