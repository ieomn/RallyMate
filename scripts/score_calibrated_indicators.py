from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from rallymate_scoring.batch import score_indicator_records
from rallymate_scoring.calibration_fitting import canonical_sha256
from rallymate_scoring.registry_lifecycle import (
    DEFAULT_REGISTRY_LIFECYCLE_PATH,
    ResolvedRegistryArtifact,
    resolve_runtime_feasibility_registry,
)
from rallymate_scoring.run_bundle_binding import (
    REQUIRED_SCORING_ARTIFACTS,
)
from rallymate_vision.pipeline import (
    load_pipeline_calibrations,
    load_runtime_view_evidence,
)
from rallymate_vision.pose.metadata import sha256_file


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest().upper()


def _load_json_document(path: Path, *, label: str) -> tuple[dict, str]:
    content = path.read_bytes()
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON {path.resolve()}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload, _sha256_bytes(content)


def _load_jsonl(
    path: Path,
    *,
    expected_sha256: str | None = None,
) -> list[dict]:
    content = path.read_bytes()
    if (
        expected_sha256 is not None
        and _sha256_bytes(content) != expected_sha256.upper()
    ):
        raise ValueError(
            f"production rescoring artifact SHA-256 mismatch: {path.name}"
        )
    try:
        text = content.decode("utf-8")
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSONL artifact {path.resolve()}: {exc}") from exc
    if any(not isinstance(record, dict) for record in records):
        raise ValueError(f"JSONL artifact must contain only objects: {path.resolve()}")
    return records


def _resolve_pinned_runtime_feasibility_registry(
    *,
    manifest_path: Path,
    pinned_registry_path: Path,
) -> ResolvedRegistryArtifact:
    return resolve_runtime_feasibility_registry(manifest_path, explicit_path=pinned_registry_path)



def _recheck_runtime_registry_authority(
    artifact: ResolvedRegistryArtifact,
    *,
    pinned_registry_path: Path,
) -> None:
    """Fail before writes if either verified authority file changed mid-run."""

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
        raise ValueError(
            "registry lifecycle manifest or authorized feasibility registry "
            "changed during scoring; refusing to write outputs"
        )


def _reject_output_path_overlap(
    *,
    output_path: Path,
    report_path: Path,
    protected_paths: list[Path | None],
) -> None:
    resolved_output = output_path.resolve()
    resolved_report = report_path.resolve()
    if resolved_output == resolved_report or (
        resolved_output.exists()
        and resolved_report.exists()
        and resolved_output.samefile(resolved_report)
    ):
        raise ValueError("score output and report paths must be different")
    protected = {path.resolve() for path in protected_paths if path is not None}
    for label, candidate in (
        ("score output", resolved_output),
        ("score report", resolved_report),
    ):
        aliases_protected_input = any(
            candidate.exists()
            and protected_path.exists()
            and candidate.samefile(protected_path)
            for protected_path in protected
        )
        if candidate in protected or aliases_protected_input:
            raise ValueError(
                f"{label} path must not overwrite a scoring or trust input: {candidate}"
            )


def _require_bound_production_artifact(
    *,
    summary: dict,
    summary_path: Path,
    artifact_name: str,
    expected_path: Path | None = None,
) -> tuple[Path, str]:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(
            "production rescoring requires scoring summary artifacts"
        )
    artifact_hashes = summary.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict):
        raise ValueError(
            "production rescoring requires scoring summary artifact_sha256"
        )
    declared = artifacts.get(artifact_name)
    if not isinstance(declared, str) or not declared:
        raise ValueError(
            f"production rescoring summary is missing artifact {artifact_name}"
        )
    declared_path = Path(declared)
    if not declared_path.is_absolute():
        declared_path = summary_path.resolve().parent / declared_path
    declared_path = declared_path.resolve()
    if expected_path is not None and declared_path != expected_path.resolve():
        raise ValueError(
            "production rescoring summary artifact does not bind the exact "
            f"{artifact_name} input"
        )
    if not declared_path.is_file():
        raise ValueError(
            f"production rescoring artifact is missing: {declared_path}"
        )
    expected_sha256 = artifact_hashes.get(artifact_name)
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise ValueError(
            "production rescoring summary is missing artifact SHA-256 for "
            f"{artifact_name}"
        )
    if (
        len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in expected_sha256
        )
    ):
        raise ValueError(
            "production rescoring summary contains an invalid artifact SHA-256: "
            f"{artifact_name}"
        )
    return declared_path, expected_sha256.upper()


def _load_calibrations(
    paths: list[Path],
    *,
    trusted_promotion_ledger: Path | None,
    feasibility_registry: dict | None,
) -> dict[str, object]:
    return load_pipeline_calibrations(paths, trusted_ledger_path=trusted_promotion_ledger,
                                      feasibility_registry=feasibility_registry)[0]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply externally supplied, versioned RallyMate calibration assets to "
            "event-level indicator features; this command never derives thresholds"
        )
    )
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--scoring-summary", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--trusted-promotion-ledger",
        type=Path,
        help=(
            "Legacy metadata path retained for compatibility; not required for mathematical scoring."
        ),
    )
    parser.add_argument(
        "--registry-lifecycle-manifest",
        type=Path,
        default=DEFAULT_REGISTRY_LIFECYCLE_PATH,
        help=(
            "Legacy default-registry manifest; an explicit registry is validated directly."
        ),
    )
    parser.add_argument(
        "--feasibility-registry",
        type=Path,
        help=(
            "Optional explicit feature/event definition registry; schema, content and runtime changes are checked."
        ),
    )
    parser.add_argument(
        "--trusted-runtime-profile-bindings",
        type=Path,
        help=(
            "Legacy profile metadata path retained for compatibility; no allow-list admission gate."
        ),
    )
    parser.add_argument(
        "--runtime-view-evidence",
        type=Path,
        help=(
            "Optional input-video identity annotation; when supplied, video_id and SHA-256 must match the records."
        ),
    )
    parser.add_argument(
        "--trusted-scoring-run-bundles",
        type=Path,
        help=(
            "Legacy bundle metadata path retained for compatibility; source artifacts are checked directly."
        ),
    )
    parser.add_argument(
        "--allow-test-only",
        action="store_true",
        help="Legacy compatibility flag; explicit mathematical rules use the same computation path.",
    )
    args = parser.parse_args()
    report_path = args.report or args.output.with_suffix(".report.json")
    _reject_output_path_overlap(
        output_path=args.output,
        report_path=report_path,
        protected_paths=[
            args.indicator_features,
            args.scoring_summary,
            *args.calibration,
            args.trusted_promotion_ledger,
            args.registry_lifecycle_manifest,
            args.feasibility_registry,
            args.trusted_runtime_profile_bindings,
            args.runtime_view_evidence,
            args.trusted_scoring_run_bundles,
        ],
    )

    original_input_hashes = {path.resolve(): sha256_file(path) for path in
        [args.indicator_features, args.scoring_summary, *args.calibration,
         *([args.runtime_view_evidence] if args.runtime_view_evidence is not None else [])]}
    summary, scoring_summary_sha256 = _load_json_document(
        args.scoring_summary, label="scoring summary"
    )
    model_versions = summary.get("model_versions")
    if not isinstance(model_versions, dict) or not model_versions:
        raise ValueError("scoring summary must contain non-empty model_versions")
    feasibility_registry_artifact = None
    feasibility_registry = None
    if args.feasibility_registry is not None:
        feasibility_registry_artifact = (
            _resolve_pinned_runtime_feasibility_registry(
                manifest_path=args.registry_lifecycle_manifest,
                pinned_registry_path=args.feasibility_registry,
            )
        )
        # The resolver hashes and parses the same raw bytes. Downstream scoring
        # must consume this payload instead of reopening the path pin.
        feasibility_registry = feasibility_registry_artifact.payload
    calibrations = _load_calibrations(
        args.calibration,
        trusted_promotion_ledger=args.trusted_promotion_ledger,
        feasibility_registry=feasibility_registry,
    )
    # Release labels and historical ledgers do not gate explicit mathematics.
    # Recheck any supplied source artifacts and their actual identity instead.
    runtime_view_evidence = None
    authoritative_events = None
    authoritative_indicators = feasibility_registry["indicators"] if feasibility_registry is not None else None
    run_bundle_ledger = None
    run_bundle_authorization = None
    bound_artifacts = {}
    declared = summary.get("artifacts", {})
    if isinstance(declared, dict):
        for name in sorted(REQUIRED_SCORING_ARTIFACTS & declared.keys()):
            exact = args.indicator_features if name == "indicator_features_jsonl" else None
            path, digest = _require_bound_production_artifact(summary=summary, summary_path=args.scoring_summary,
                artifact_name=name, expected_path=exact)
            if sha256_file(path) != digest:
                raise ValueError(f"rescoring artifact SHA-256 mismatch: {name}")
            bound_artifacts[name] = (path, digest)
    _reject_output_path_overlap(output_path=args.output, report_path=report_path,
                                protected_paths=[path for path, _ in bound_artifacts.values()])
    feature_digest = bound_artifacts.get("indicator_features_jsonl", (None, None))[1]
    records = _load_jsonl(args.indicator_features, expected_sha256=feature_digest)
    events_path, events_digest = bound_artifacts.get("events_jsonl", (args.indicator_features.with_name("events.jsonl"), None))
    if events_path.is_file():
        events_digest = events_digest or sha256_file(events_path)
        authoritative_events = _load_jsonl(events_path, expected_sha256=events_digest)
    expected_video = {"video_id": summary.get("video_id"),
                      "video_sha256": summary.get("provenance", {}).get("video_sha256")}
    if args.runtime_view_evidence is not None:
        runtime_view_evidence = load_runtime_view_evidence(args.runtime_view_evidence)
        for field in expected_video:
            expected = expected_video[field]
            supplied = runtime_view_evidence[field]
            if expected is not None and str(expected).lower() != str(supplied).lower():
                raise ValueError(f"scoring summary {field} does not match runtime view evidence")
            expected_video[field] = supplied
    for index, record in enumerate(records):
        for field, expected in expected_video.items():
            if expected is None:
                continue
            actual = record.get("provenance", {}).get(field) if field == "video_sha256" else record.get(field)
            if actual is None or str(actual).lower() != str(expected).lower():
                raise ValueError(f"indicator feature {field} differs from source video at record {index}")
    source_hashes = {path.resolve(): sha256_file(path) for path in
        [args.indicator_features, args.scoring_summary, *args.calibration,
         *([events_path] if authoritative_events is not None else []),
         *([args.runtime_view_evidence] if args.runtime_view_evidence is not None else [])]}
    source_hashes.update(original_input_hashes)
    if authoritative_events is not None:
        source_hashes[events_path.resolve()] = events_digest
    if feasibility_registry is not None:
        model_versions = {
            **model_versions,
            "runtime_feasibility_registry": feasibility_registry[
                "registry_version"
            ],
            "runtime_feasibility_registry_sha256": canonical_sha256(
                feasibility_registry
            ),
        }
    outputs, report = score_indicator_records(
        records,
        calibrations=calibrations,
        model_versions=model_versions,
        allow_test_only=args.allow_test_only,
        authoritative_events=authoritative_events,
        authoritative_indicators=authoritative_indicators,
    )
    if feasibility_registry_artifact is not None:
        _recheck_runtime_registry_authority(
            feasibility_registry_artifact,
            pinned_registry_path=args.feasibility_registry,
        )
    for path, digest in source_hashes.items():
        if sha256_file(path) != digest:
            raise ValueError(f"scoring input changed during computation: {path.name}")
    for path, digest in bound_artifacts.values():
        if sha256_file(path) != digest:
            raise ValueError(f"scoring source artifact changed during computation: {path.name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in outputs),
        encoding="utf-8",
    )
    report.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inputs": {
                "indicator_features": str(args.indicator_features.resolve()),
                "events": (
                    str(args.indicator_features.resolve().with_name("events.jsonl"))
                    if authoritative_events is not None
                    else None
                ),
                "scoring_summary": str(args.scoring_summary.resolve()),
                "calibrations": [str(path.resolve()) for path in args.calibration],
                "trusted_promotion_ledger": (
                    str(args.trusted_promotion_ledger.resolve())
                    if args.trusted_promotion_ledger is not None
                    else None
                ),
                "feasibility_registry": (
                    str(feasibility_registry_artifact.path)
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "feasibility_registry_path_pin": (
                    str(args.feasibility_registry.resolve())
                    if args.feasibility_registry is not None
                    else None
                ),
                "feasibility_registry_version": (
                    feasibility_registry["registry_version"]
                    if feasibility_registry is not None
                    else None
                ),
                "feasibility_registry_canonical_sha256": (
                    canonical_sha256(feasibility_registry)
                    if feasibility_registry is not None
                    else None
                ),
                "feasibility_registry_raw_file_sha256": (
                    feasibility_registry_artifact.file_sha256
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "feasibility_registry_kind": (
                    feasibility_registry_artifact.kind
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "feasibility_registry_lifecycle": (
                    feasibility_registry_artifact.lifecycle
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "feasibility_registry_relative_path": (
                    feasibility_registry_artifact.relative_path
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "registry_lifecycle_manifest": (
                    str(feasibility_registry_artifact.manifest_path)
                    if feasibility_registry_artifact is not None and feasibility_registry_artifact.manifest_path is not None
                    else None
                ),
                "registry_lifecycle_manifest_sha256": (
                    feasibility_registry_artifact.manifest_sha256
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "registry_lifecycle_authority_version": (
                    feasibility_registry_artifact.authority_version
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "registry_lifecycle_authority_slot": (
                    feasibility_registry_artifact.authority_slot
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "registry_lifecycle_role": (
                    feasibility_registry_artifact.role
                    if feasibility_registry_artifact is not None
                    else None
                ),
                "trusted_runtime_profile_bindings": (
                    str(args.trusted_runtime_profile_bindings.resolve())
                    if args.trusted_runtime_profile_bindings is not None
                    else None
                ),
                "trusted_runtime_profile_bindings_sha256": (
                    sha256_file(args.trusted_runtime_profile_bindings.resolve())
                    if args.trusted_runtime_profile_bindings is not None and args.trusted_runtime_profile_bindings.is_file()
                    else None
                ),
                "runtime_view_evidence": (
                    str(args.runtime_view_evidence.resolve())
                    if args.runtime_view_evidence is not None
                    else None
                ),
                "runtime_view_evidence_sha256": (
                    sha256_file(args.runtime_view_evidence.resolve())
                    if args.runtime_view_evidence is not None
                    else None
                ),
                "trusted_scoring_run_bundles": (
                    str(args.trusted_scoring_run_bundles.resolve())
                    if args.trusted_scoring_run_bundles is not None
                    else None
                ),
                "trusted_scoring_run_bundles_canonical_sha256": (
                    canonical_sha256(run_bundle_ledger)
                    if run_bundle_ledger is not None
                    else None
                ),
                "trusted_scoring_run_bundle_entry_id": (
                    run_bundle_authorization["entry_id"]
                    if run_bundle_authorization is not None
                    else None
                ),
                "trusted_scoring_run_bundle_ledger_id": (
                    run_bundle_authorization["ledger_id"]
                    if run_bundle_authorization is not None
                    else None
                ),
                "trusted_scoring_run_bundle_ledger_version": (
                    run_bundle_authorization["ledger_version"]
                    if run_bundle_authorization is not None
                    else None
                ),
                "trusted_scoring_run_bundle_authority_id": (
                    run_bundle_authorization["ledger_authority_id"]
                    if run_bundle_authorization is not None
                    else None
                ),
                "trusted_scoring_run_bundle_review_id": (
                    run_bundle_authorization["review_id"]
                    if run_bundle_authorization is not None
                    else None
                ),
                "trusted_scoring_run_bundle_reviewer_id": (
                    run_bundle_authorization["reviewer_id"]
                    if run_bundle_authorization is not None
                    else None
                ),
                "scoring_run_bundle_root_sha256": (
                    run_bundle_authorization["bundle_root_sha256"]
                    if run_bundle_authorization is not None
                    else None
                ),
                "scoring_summary_sha256": scoring_summary_sha256,
            },
            "output": str(args.output.resolve()),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "report": str(report_path),
                "status_counts": report["status_counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
