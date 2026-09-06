from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_vision.validation import validate_run_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate RallyMate stage-1 output invariants"
    )
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    result = validate_run_artifacts(args.output_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
