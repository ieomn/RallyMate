from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.measurement_plans import write_measurement_plan_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile all RallyMate metric cards into traceable measurement plans")
    parser.add_argument("--output", type=Path, default=Path("metric-measurement-plans.json"))
    parser.add_argument(
        "--feasibility-registry",
        type=Path,
        default=Path("metric-feasibility-pose-wave-v2.json"),
        help="Canonical versioned feasibility registry used to derive current F2 state",
    )
    args = parser.parse_args()
    output = write_measurement_plan_registry(
        args.output,
        feasibility_registry=args.feasibility_registry,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "output": str(output),
                "indicator_count": payload["summary"]["indicator_count"],
                "runtime_status_counts": payload["summary"]["runtime_status_counts"],
                "current_f2_indicator_count": payload["summary"]["current_f2_indicator_count"],
                "next_pose_wave_count": payload["summary"]["next_pose_wave_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
