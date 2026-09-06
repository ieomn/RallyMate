from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from rallymate_scoring.calibration_promotion import (
    CalibrationPromotionError,
    build_trusted_promotion_ledger,
    promote_calibration_candidate,
)
from rallymate_scoring.calibration_fitting import canonical_sha256
from rallymate_scoring.maturity_evidence import (
    MaturityEvidenceError,
    PRODUCTION_SCOPE as MATURITY_PRODUCTION_SCOPE,
    TEST_ONLY_SCOPE as MATURITY_TEST_ONLY_SCOPE,
    validate_maturity_evidence_for_registry,
)
from rallymate_scoring.registry_lifecycle import (
    DEFAULT_REGISTRY_LIFECYCLE_PATH,
    RegistryLifecycleError,
    ResolvedRegistryArtifact,
    resolve_runtime_feasibility_registry,
)


def _read_snapshot(path: Path) -> tuple[bytes, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CalibrationPromotionError(f"cannot read {path}: {exc}") from exc
    return raw, hashlib.sha256(raw).hexdigest()


def _strict_json_loads(text: str, path: Path, *, line_number: int | None = None):
    """Parse promotion inputs without ambiguous keys or non-finite numbers."""

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str):
        raise ValueError(f"non-finite JSON constant: {value}")

    def parse_finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"non-finite JSON number: {value}")
        return result

    def validate_canonical_utf8(value) -> None:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "JSON contains a string that cannot be encoded as UTF-8"
                ) from exc
            return
        if isinstance(value, list):
            for item in value:
                validate_canonical_utf8(item)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                validate_canonical_utf8(key)
                validate_canonical_utf8(item)
            return
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("JSON contains a non-finite number")

    location = f"{path} line {line_number}" if line_number is not None else str(path)
    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
            parse_float=parse_finite_float,
        )
        validate_canonical_utf8(payload)
        return payload
    except (ValueError, json.JSONDecodeError) as exc:
        raise CalibrationPromotionError(f"cannot parse {location}: {exc}") from exc


def _load_object_snapshot(path: Path) -> tuple[dict, dict]:
    raw, raw_sha256 = _read_snapshot(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CalibrationPromotionError(f"cannot read {path}: {exc}") from exc
    payload = _strict_json_loads(text, path)
    if not isinstance(payload, dict):
        raise CalibrationPromotionError(f"{path} must contain a JSON object")
    return payload, {
        "source_kind": "file_bytes",
        "source_path": str(path.resolve()),
        "raw_sha256": raw_sha256,
        "canonical_sha256": canonical_sha256(payload),
    }


def _load_jsonl_snapshot(path: Path) -> tuple[list[dict], dict]:
    raw, raw_sha256 = _read_snapshot(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CalibrationPromotionError(f"cannot read {path}: {exc}") from exc
    records: list[dict] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        payload = _strict_json_loads(line, path, line_number=line_number)
        if not isinstance(payload, dict):
            raise CalibrationPromotionError(
                f"{path} line {line_number} must contain a JSON object"
            )
        records.append(payload)
    return records, {
        "source_kind": "file_bytes",
        "source_path": str(path.resolve()),
        "raw_sha256": raw_sha256,
        "canonical_sha256": canonical_sha256(records),
        "source_record_count": len(records),
    }


def _recheck_snapshot(path: Path, expected_raw_sha256: str) -> None:
    _, actual = _read_snapshot(path)
    if actual != expected_raw_sha256:
        raise CalibrationPromotionError(
            f"input changed during promotion: {path}"
        )


def _resolve_pinned_runtime_feasibility_registry(
    *,
    manifest_path: Path,
    pinned_registry_path: Path,
) -> ResolvedRegistryArtifact:
    try:
        artifact = resolve_runtime_feasibility_registry(manifest_path)
    except (FileNotFoundError, OSError, RegistryLifecycleError) as exc:
        raise CalibrationPromotionError(
            f"invalid registry lifecycle authority: {exc}"
        ) from exc
    try:
        resolved_pin = pinned_registry_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise CalibrationPromotionError(
            "--feasibility-registry path pin is missing: "
            f"{pinned_registry_path}"
        ) from exc
    if resolved_pin != artifact.path:
        raise CalibrationPromotionError(
            "--feasibility-registry must exactly match the lifecycle-authorized "
            "runtime feasibility registry path; copies, historical registries, "
            f"and unregistered paths are rejected (authorized: {artifact.path})"
        )
    return artifact


def _recheck_runtime_registry_authority(
    artifact: ResolvedRegistryArtifact,
    *,
    pinned_registry_path: Path,
) -> None:
    """Refuse production output if either authority file changed mid-run."""

    rechecked = _resolve_pinned_runtime_feasibility_registry(
        manifest_path=artifact.manifest_path,
        pinned_registry_path=pinned_registry_path,
    )
    if (
        rechecked.manifest_path != artifact.manifest_path
        or rechecked.manifest_sha256 != artifact.manifest_sha256
        or rechecked.authority_version != artifact.authority_version
        or rechecked.authority_slot != artifact.authority_slot
        or rechecked.path != artifact.path
        or rechecked.file_sha256 != artifact.file_sha256
        or rechecked.embedded_version != artifact.embedded_version
    ):
        raise CalibrationPromotionError(
            "registry lifecycle manifest or authorized feasibility registry "
            "changed during promotion; refusing to write outputs"
        )


def _stage_json(path: Path, payload: dict) -> Path:
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
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _write_outputs(
    asset_path: Path,
    asset: dict,
    report_path: Path,
    report: dict,
    ledger_path: Path | None = None,
    ledger: dict | None = None,
) -> None:
    outputs = [("asset", asset_path, asset), ("promotion report", report_path, report)]
    if (ledger_path is None) != (ledger is None):
        raise CalibrationPromotionError("ledger output path and payload must be provided together")
    if ledger_path is not None and ledger is not None:
        outputs.append(("trusted promotion ledger", ledger_path, ledger))
    resolved = [path.resolve() for _, path, _ in outputs]
    if len(resolved) != len(set(resolved)):
        raise CalibrationPromotionError("promotion output paths must differ")
    for label, path, _ in outputs:
        if path.exists():
            raise CalibrationPromotionError(f"refusing to overwrite {label}: {path}")
    staged: list[tuple[Path, Path]] = []
    try:
        staged = [(_stage_json(path, payload), path) for _, path, payload in outputs]
        # All validation and serialization have succeeded before either destination
        # appears. Each final file is installed with an atomic same-directory replace.
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Promote a passed calibration candidate only after an explicit human "
            "decision and an already-F4 feasibility registry agree. Synthetic "
            "candidates can create test-only assets only. Production promotion "
            "also requires a complete F0-to-F4 maturity-evidence bundle. No fitted "
            "numeric parameter is estimated or modified by this command. Real "
            "promotion remains fail-closed until the authorized-intake verifier "
            "is integrated in-process; serialized lineage cannot supply authority."
        )
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--independent-test-report", type=Path, required=True)
    parser.add_argument(
        "--independent-test-protocol",
        type=Path,
        help=(
            "exact preregistered protocol payload used by the evaluator; "
            "mandatory for production promotion"
        ),
    )
    parser.add_argument(
        "--independent-test-samples",
        type=Path,
        help=(
            "sealed independent-test samples as JSONL; mandatory for production "
            "promotion so the supplied report is replayed from source"
        ),
    )
    parser.add_argument(
        "--indicator-requirements",
        type=Path,
        help=(
            "authoritative indicator requirements JSON used by independent-test "
            "replay; mandatory for production promotion"
        ),
    )
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--feasibility-registry", type=Path, required=True)
    parser.add_argument(
        "--registry-lifecycle-manifest",
        type=Path,
        default=DEFAULT_REGISTRY_LIFECYCLE_PATH,
        help=(
            "operator-controlled lifecycle authority whose runtime_feasibility "
            "role is mandatory for production promotion"
        ),
    )
    parser.add_argument(
        "--maturity-evidence",
        type=Path,
        help=(
            "complete versioned F0-to-F4 maturity-evidence bundle; required for "
            "production and optional synthetic-test-only promotion"
        ),
    )
    parser.add_argument("--promoted-at", required=True)
    parser.add_argument("--asset-output", type=Path, required=True)
    parser.add_argument("--promotion-report-output", type=Path, required=True)
    parser.add_argument("--trusted-ledger-output", type=Path)
    parser.add_argument("--ledger-id")
    parser.add_argument("--ledger-version")
    parser.add_argument("--ledger-authority-id")
    parser.add_argument("--ledger-registered-at")
    args = parser.parse_args()
    try:
        candidate, candidate_source = _load_object_snapshot(args.candidate)
        (
            independent_test_report,
            independent_test_report_source,
        ) = _load_object_snapshot(args.independent_test_report)
        independent_test_protocol = None
        independent_test_protocol_source = None
        if args.independent_test_protocol is not None:
            (
                independent_test_protocol,
                independent_test_protocol_source,
            ) = _load_object_snapshot(args.independent_test_protocol)
        decision, decision_source = _load_object_snapshot(args.decision)
        requested_scope = decision.get("requested_artifact_scope")
        if requested_scope == "production" and independent_test_protocol is None:
            raise CalibrationPromotionError(
                "production promotion requires --independent-test-protocol"
            )
        if requested_scope == "production" and args.independent_test_samples is None:
            raise CalibrationPromotionError(
                "production promotion requires --independent-test-samples"
            )
        if requested_scope == "production" and args.indicator_requirements is None:
            raise CalibrationPromotionError(
                "production promotion requires --indicator-requirements"
            )
        independent_test_samples = None
        independent_test_samples_source = None
        if args.independent_test_samples is not None:
            (
                independent_test_samples,
                independent_test_samples_source,
            ) = _load_jsonl_snapshot(args.independent_test_samples)
            if not isinstance(candidate.get("independent_test"), dict):
                raise CalibrationPromotionError(
                    "candidate.independent_test is required for source replay"
                )
            independent_test_samples_source["sealed_record_count"] = candidate[
                "independent_test"
            ].get("record_count")
        indicator_requirements = None
        indicator_requirements_source = None
        if args.indicator_requirements is not None:
            (
                indicator_requirements,
                indicator_requirements_source,
            ) = _load_object_snapshot(args.indicator_requirements)
        independent_test_source_replay = (
            {
                "samples": independent_test_samples_source,
                "indicator_requirements": indicator_requirements_source,
            }
            if independent_test_samples_source is not None
            and indicator_requirements_source is not None
            else None
        )
        registry_authority = None
        feasibility_registry_source = None
        if requested_scope == "production":
            registry_authority = _resolve_pinned_runtime_feasibility_registry(
                manifest_path=args.registry_lifecycle_manifest,
                pinned_registry_path=args.feasibility_registry,
            )
            # Use the JSON parsed from the exact bytes verified by the lifecycle
            # resolver; never reopen the production registry after verification.
            feasibility_registry = registry_authority.payload
        else:
            feasibility_registry, feasibility_registry_source = _load_object_snapshot(
                args.feasibility_registry
            )
        if requested_scope == "production" and args.maturity_evidence is None:
            raise CalibrationPromotionError(
                "production promotion requires --maturity-evidence"
            )
        maturity_evidence = None
        maturity_evidence_source = None
        if args.maturity_evidence is not None:
            (
                maturity_evidence,
                maturity_evidence_source,
            ) = _load_object_snapshot(args.maturity_evidence)
        if maturity_evidence is not None:
            expected_maturity_scope = (
                MATURITY_PRODUCTION_SCOPE
                if requested_scope == "production"
                else MATURITY_TEST_ONLY_SCOPE
            )
            try:
                validate_maturity_evidence_for_registry(
                    maturity_evidence,
                    feasibility_registry,
                    expected_scope=expected_maturity_scope,
                )
            except MaturityEvidenceError as exc:
                raise CalibrationPromotionError(
                    f"invalid maturity evidence: {exc}"
                ) from exc
        promotion_input_snapshots = None
        if requested_scope == "production":
            assert independent_test_protocol_source is not None
            assert maturity_evidence_source is not None
            assert registry_authority is not None
            promotion_input_snapshots = {
                "candidate": candidate_source,
                "independent_test_report": independent_test_report_source,
                "independent_test_protocol": independent_test_protocol_source,
                "decision": decision_source,
                "maturity_evidence": maturity_evidence_source,
                "registry_lifecycle_authority": {
                    "source_kind": "registry_lifecycle_verified_file_bytes",
                    "manifest_path": str(registry_authority.manifest_path),
                    "manifest_raw_sha256": registry_authority.manifest_sha256,
                    "authority_version": registry_authority.authority_version,
                    "authority_slot": registry_authority.authority_slot,
                    "artifact_path": str(registry_authority.path),
                    "artifact_raw_sha256": registry_authority.file_sha256,
                    "artifact_canonical_sha256": canonical_sha256(
                        registry_authority.payload
                    ),
                    "embedded_version": registry_authority.embedded_version,
                },
            }
        asset, report = promote_calibration_candidate(
            candidate=candidate,
            independent_test_report=independent_test_report,
            independent_test_protocol=independent_test_protocol,
            independent_test_samples=independent_test_samples,
            indicator_requirements=indicator_requirements,
            independent_test_source_replay=independent_test_source_replay,
            promotion_input_snapshots=promotion_input_snapshots,
            decision=decision,
            feasibility_registry=feasibility_registry,
            maturity_evidence=maturity_evidence,
            promoted_at=args.promoted_at,
        )
        ledger = None
        ledger_fields = (
            args.trusted_ledger_output,
            args.ledger_id,
            args.ledger_version,
            args.ledger_authority_id,
            args.ledger_registered_at,
        )
        if asset["artifact_scope"] == "production":
            if any(value is None for value in ledger_fields):
                raise CalibrationPromotionError(
                    "production promotion requires --trusted-ledger-output, "
                    "--ledger-id, --ledger-version, --ledger-authority-id and "
                    "--ledger-registered-at"
                )
            ledger = build_trusted_promotion_ledger(
                promotions=[(asset, report)],
                ledger_id=args.ledger_id,
                ledger_version=args.ledger_version,
                authority_id=args.ledger_authority_id,
                registered_at=args.ledger_registered_at,
            )
        elif any(value is not None for value in ledger_fields):
            raise CalibrationPromotionError(
                "synthetic/test-only promotions cannot create a trusted production ledger"
            )
        if registry_authority is not None:
            _recheck_runtime_registry_authority(
                registry_authority,
                pinned_registry_path=args.feasibility_registry,
            )
        snapshot_inputs = [
            (args.candidate, candidate_source),
            (args.independent_test_report, independent_test_report_source),
            (args.decision, decision_source),
        ]
        for input_path, source in (
            (args.independent_test_protocol, independent_test_protocol_source),
            (args.maturity_evidence, maturity_evidence_source),
            (args.independent_test_samples, independent_test_samples_source),
            (args.indicator_requirements, indicator_requirements_source),
        ):
            if input_path is not None and source is not None:
                snapshot_inputs.append((input_path, source))
        if feasibility_registry_source is not None:
            snapshot_inputs.append(
                (args.feasibility_registry, feasibility_registry_source)
            )
        for input_path, source in snapshot_inputs:
            _recheck_snapshot(input_path, source["raw_sha256"])
        _write_outputs(
            args.asset_output,
            asset,
            args.promotion_report_output,
            report,
            args.trusted_ledger_output,
            ledger,
        )
    except CalibrationPromotionError as exc:
        parser.exit(2, f"calibration promotion rejected: {exc}\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "artifact_scope": asset["artifact_scope"],
                "indicator_id": asset["indicator_id"],
                "asset_output": str(args.asset_output.resolve()),
                "promotion_report_output": str(args.promotion_report_output.resolve()),
                "maturity_evidence_input": (
                    str(args.maturity_evidence.resolve())
                    if args.maturity_evidence is not None
                    else None
                ),
                "trusted_ledger_output": (
                    str(args.trusted_ledger_output.resolve())
                    if args.trusted_ledger_output is not None
                    else None
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
