from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.calibration_ranking import (
    RankingCalibrationError,
    fit_ranking_candidate,
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
            "Fit a non-scoring relative-order candidate from coach rankings. The "
            "candidate has no A-E grade, thresholds, cutpoints or production authority."
        )
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--fitted-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise RankingCalibrationError(f"refusing to overwrite output: {args.output}")
        candidate = fit_ranking_candidate(
            _load_object(args.dataset),
            _load_object(args.protocol),
            fitted_at=args.fitted_at,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, candidate)
    except (OSError, RankingCalibrationError, ValueError) as exc:
        parser.exit(2, f"ranking candidate fit rejected: {exc}\n")
    print(
        json.dumps(
            {
                "status": "ranking_candidate_created_not_scored",
                "output": str(args.output.resolve()),
                "candidate_version": candidate["candidate_version"],
                "target": "relative_order_only",
                "grade": None,
                "threshold_version": None,
                "A_E_scoring_ready": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
