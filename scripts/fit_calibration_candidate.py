from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    fit_calibration_candidates,
)


def _load_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationFitError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CalibrationFitError(f"{path} must contain a JSON object")
    return payload


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            "Fit non-production RallyMate calibration candidates from a prepared "
            "human-ground-truth dataset and preregistered protocol. Independent-test "
            "labels remain sealed and this command never emits a production asset. "
            "Real fitting remains fail-closed until the authorized-intake verifier "
            "can supply a same-process non-serializable authorization object."
        )
    )
    parser.add_argument("--prepared-dataset", type=Path, required=True)
    parser.add_argument("--fit-protocol", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()

    try:
        dataset = _load_object(args.prepared_dataset)
        protocol = _load_object(args.fit_protocol)
        if (
            dataset.get("artifact_scope") != "calibration_input"
            or protocol.get("artifact_scope") != "calibration_fit_protocol"
        ):
            raise CalibrationFitError(
                "the file-producing CLI accepts real human-ground-truth inputs only; "
                "synthetic fixtures are restricted to in-memory unit tests"
            )
        candidates = fit_calibration_candidates(dataset, protocol)
        if not candidates:
            raise CalibrationFitError("fit protocol produced no candidates")
        if args.output_directory.exists() and not args.output_directory.is_dir():
            raise CalibrationFitError("output-directory exists and is not a directory")
        targets = [
            args.output_directory
            / f"calibration-candidate-{candidate['candidate_version'][-16:]}.json"
            for candidate in candidates
        ]
        existing = [path for path in targets if path.exists()]
        if existing:
            raise CalibrationFitError(
                "refusing to overwrite existing candidate assets: "
                + ", ".join(str(path) for path in existing)
            )
        # All validation and fitting finishes before the first asset is written.
        for target, candidate in zip(targets, candidates):
            _atomic_write_json(target, candidate)
    except CalibrationFitError as exc:
        parser.exit(2, f"calibration candidate fit rejected: {exc}\n")

    print(
        json.dumps(
            {
                "status": "calibration_candidates_created_not_scoring_assets",
                "artifact_scope": candidates[0]["artifact_scope"],
                "indicator_id": candidates[0]["indicator_id"],
                "outputs": [str(path.resolve()) for path in targets],
                "independent_test_accessed": False,
                "scoring_allowed": False,
                "F3_promoted": False,
                "F4_promoted": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
