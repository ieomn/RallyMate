from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from rallymate_scoring.calibration_independent_test import (
    IndependentTestError,
    evaluate_independent_test,
    load_jsonl,
)


def _load_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IndependentTestError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IndependentTestError(f"{path} must contain a JSON object")
    return payload


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise IndependentTestError(f"refusing to overwrite report: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Open one sealed independent-test split, verify its canonical hash, "
            "evaluate a non-production calibration candidate and apply only the "
            "limits declared in an external preregistered protocol. This command "
            "never creates or approves a production scoring asset."
        )
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--indicator-requirements", type=Path, required=True)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = evaluate_independent_test(
            candidate=_load_object(args.candidate),
            samples=load_jsonl(args.samples),
            protocol=_load_object(args.protocol),
            indicator_requirements=_load_object(args.indicator_requirements),
            evaluated_at=args.evaluated_at,
        )
        _atomic_write(args.output, report)
    except IndependentTestError as exc:
        parser.exit(2, f"independent calibration test rejected: {exc}\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "approved_for_scoring": False,
                "output": str(args.output.resolve()),
                "evaluated_records": report["coverage"]["evaluated_record_count"],
                "production_asset_created": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
