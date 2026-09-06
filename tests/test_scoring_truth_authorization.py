from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from jsonschema import Draft202012Validator, FormatChecker

import rallymate_annotation.scoring_truth_authorization as authorization
from rallymate_annotation.scoring_truth_authorization import (
    ScoringTruthAuthorizationError,
    scoring_truth_authorization_binding_sha256,
    validate_scoring_truth_authorization_binding,
    verify_scoring_truth_event_authorization,
)


ROOT = Path(__file__).resolve().parents[1]
HANDOFF_DIR = ROOT / "data" / "annotations" / "scoring-truth-event-handoff-m89-v1"
PLAN_SCHEMA = ROOT / "contracts" / "scoring-truth-event-annotation-plan.schema.json"
RELEASE_SCHEMA = (
    ROOT / "contracts" / "scoring-truth-operator-release-record.schema.json"
)
RELEASE_TEMPLATE = (
    ROOT / "examples" / "scoring-truth-operator-release-record.template.json"
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _write_json(path: Path, value: Any) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )
    path.write_bytes(raw)
    return raw


def _manifest_technical() -> tuple[dict[str, Any], dict[str, Any]]:
    path = HANDOFF_DIR / "event-handoff-manifest.json"
    raw = path.read_bytes()
    manifest = json.loads(raw)
    return manifest, {
        "manifest_raw_sha256": _sha256(raw),
        "bundle_version": manifest["bundle_version"],
        "bundle_id": manifest["bundle_id"],
        "content_root_sha256": manifest["content_root_sha256"],
        "source_projection_sha256": manifest["source_authority"][
            "source_contract_sha256"
        ],
    }


def _plan(technical: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "plan_version": "scoring-truth-event-annotation-plan-v2.0.0",
        "plan_id": "m90-independent-event-phase-plan-002",
        "created_at": "2026-08-30T03:42:00Z",
        "frozen_at": "2026-08-30T03:43:00Z",
        "artifact_scope": "scoring_truth_event_annotation_plan",
        "technical_handoff": {
            "manifest_path": "event-handoff-manifest.json",
            **technical,
        },
        "scope": {
            "event_codes": copy.deepcopy(authorization.EVENT_CODES),
            "phase_keys_by_event": copy.deepcopy(authorization.PHASE_KEYS_BY_EVENT),
            "tasks": copy.deepcopy(authorization.TASKS),
        },
        "roles": copy.deepcopy(authorization.ROLES),
        "decision_contract": copy.deepcopy(authorization.DECISION_CONTRACT),
        "data_access": copy.deepcopy(authorization.DATA_ACCESS),
        "safety": copy.deepcopy(authorization.SAFETY),
    }


def _release_record(
    plan: dict[str, Any], plan_raw: bytes, technical: dict[str, Any]
) -> dict[str, Any]:
    role_protocol = {
        "roles": plan["roles"],
        "decision_contract": plan["decision_contract"],
        "data_access": plan["data_access"],
        "safety": plan["safety"],
    }
    return {
        "schema_version": "1.0.0",
        "release_version": "scoring-truth-operator-reviewed-local-release-v1.0.0",
        "artifact_scope": "scoring_truth_operator_reviewed_local_release",
        "release_id": "m90-local-operator-release-001",
        "released_at": "2026-08-30T03:44:00Z",
        "reviewed_by": {
            "reviewer_id": "annotation-operations-reviewer-01",
            "role": "annotation_release_operator",
        },
        "decision": "operator_released_for_independent_event_phase_annotation",
        "plan": {
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "raw_sha256": _sha256(plan_raw),
        },
        "technical_handoff": copy.deepcopy(technical),
        "scope_digest_sha256": _sha256(_canonical_bytes(plan["scope"])),
        "role_protocol_digest_sha256": _sha256(_canonical_bytes(role_protocol)),
        "attestations": copy.deepcopy(authorization.RELEASE_ATTESTATIONS),
        "safety": copy.deepcopy(authorization.RELEASE_SAFETY),
    }


def _make_evidence(work_dir: Path, release_dir: Path) -> dict[str, Any]:
    _, technical = _manifest_technical()
    plan = _plan(technical)
    plan_path = work_dir / "plan.json"
    plan_raw = _write_json(plan_path, plan)
    release = _release_record(plan, plan_raw, technical)
    release_path = release_dir / "operator-release.json"
    release_raw = _write_json(release_path, release)
    return {
        "technical": technical,
        "plan": plan,
        "plan_raw": plan_raw,
        "plan_path": plan_path,
        "release": release,
        "release_raw": release_raw,
        "release_path": release_path,
    }


def _verify(evidence: dict[str, Any], *, handoff_dir: Path = HANDOFF_DIR) -> dict[str, Any]:
    return verify_scoring_truth_event_authorization(
        plan_path=evidence["plan_path"],
        release_record_path=evidence["release_path"],
        handoff_dir=handoff_dir,
    )


class ScoringTruthAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.work_context = tempfile.TemporaryDirectory(prefix="rallymate-m90-work-")
        self.release_context = tempfile.TemporaryDirectory(
            prefix="rallymate-m90-local-release-"
        )
        self.work_dir = Path(self.work_context.name)
        self.evidence = _make_evidence(
            self.work_dir, Path(self.release_context.name)
        )

    def tearDown(self) -> None:
        self.release_context.cleanup()
        self.work_context.cleanup()

    def test_valid_local_operator_release_verifies_exact_m89_and_schemas(self) -> None:
        evidence = self.evidence
        binding = _verify(evidence)

        self.assertEqual(binding["binding_version"], authorization.BINDING_VERSION)
        self.assertEqual(binding["status"], "operator_reviewed_local_release_verified")
        self.assertIs(binding["operator_release_record_verified"], True)
        self.assertIs(binding["annotation_workflow_release_only"], True)
        self.assertEqual(binding["plan"]["raw_sha256"], _sha256(evidence["plan_raw"]))
        self.assertEqual(
            binding["release_record"]["raw_sha256"],
            _sha256(evidence["release_raw"]),
        )
        self.assertEqual(binding["technical_handoff"], evidence["technical"])
        self.assertNotIn("receipt", binding)
        self.assertNotIn("trust_anchor", binding)
        self.assertNotIn("signature_verified", binding)
        self.assertEqual(
            binding["binding_sha256"],
            scoring_truth_authorization_binding_sha256(binding),
        )
        self.assertIsNone(validate_scoring_truth_authorization_binding(binding))

        checker = FormatChecker()
        for schema_path, instance in (
            (PLAN_SCHEMA, evidence["plan"]),
            (RELEASE_SCHEMA, evidence["release"]),
        ):
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema, format_checker=checker).validate(instance)

    def test_v1_plan_is_rejected_as_incompatible(self) -> None:
        self.evidence["plan"]["plan_version"] = (
            "scoring-truth-event-annotation-plan-v1.0.0"
        )
        _write_json(self.evidence["plan_path"], self.evidence["plan"])

        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "plan_version"):
            _verify(self.evidence)

    def test_release_binds_exact_plan_bytes(self) -> None:
        self.evidence["plan"]["frozen_at"] = "2026-08-30T03:43:01Z"
        _write_json(self.evidence["plan_path"], self.evidence["plan"])

        with self.assertRaisesRegex(
            ScoringTruthAuthorizationError, "exact annotation-plan bytes"
        ):
            _verify(self.evidence)

    def test_json_boolean_cannot_be_substituted_with_numeric_one(self) -> None:
        self.evidence["plan"]["roles"]["annotator_blinding_required"] = 1
        _write_json(self.evidence["plan_path"], self.evidence["plan"])

        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "plan.roles"):
            _verify(self.evidence)

    def test_verification_uses_captured_plan_and_release_bytes_without_reopening(self) -> None:
        evidence = self.evidence
        original_snapshot_json = authorization._snapshot_json

        def mutate_after_snapshot(path: str | Path, *, name: str):
            snapshot = original_snapshot_json(path, name=name)
            if name in {"annotation plan", "operator release record"}:
                Path(path).write_bytes(b'{"replaced_after_snapshot":true}\n')
            return snapshot

        with mock.patch.object(
            authorization, "_snapshot_json", side_effect=mutate_after_snapshot
        ):
            binding = _verify(evidence)

        self.assertEqual(binding["plan"]["raw_sha256"], _sha256(evidence["plan_raw"]))
        self.assertEqual(
            binding["release_record"]["raw_sha256"], _sha256(evidence["release_raw"])
        )
        self.assertNotEqual(evidence["plan_path"].read_bytes(), evidence["plan_raw"])
        self.assertNotEqual(
            evidence["release_path"].read_bytes(), evidence["release_raw"]
        )

    def test_release_must_bind_all_exact_lineage_hashes(self) -> None:
        original = copy.deepcopy(self.evidence["release"])
        mutations = (
            ("plan", lambda item: item["plan"].__setitem__("raw_sha256", "0" * 64)),
            (
                "validated M89 handoff",
                lambda item: item["technical_handoff"].__setitem__(
                    "content_root_sha256", "0" * 64
                ),
            ),
            (
                "scope digest",
                lambda item: item.__setitem__("scope_digest_sha256", "0" * 64),
            ),
            (
                "role protocol digest",
                lambda item: item.__setitem__(
                    "role_protocol_digest_sha256", "0" * 64
                ),
            ),
        )
        for expected_error, mutate in mutations:
            with self.subTest(expected_error=expected_error):
                changed = copy.deepcopy(original)
                mutate(changed)
                _write_json(self.evidence["release_path"], changed)
                with self.assertRaisesRegex(
                    ScoringTruthAuthorizationError, expected_error
                ):
                    _verify(self.evidence)

    def test_operator_review_and_annotation_only_limits_are_exact(self) -> None:
        original = copy.deepcopy(self.evidence["release"])
        mutations = (
            (
                "reviewer role",
                lambda item: item["reviewed_by"].__setitem__("role", "calibration_operator"),
            ),
            (
                "decision",
                lambda item: item.__setitem__("decision", "released_for_calibration"),
            ),
            (
                "attestations",
                lambda item: item["attestations"].__setitem__(
                    "exact_plan_reviewed", False
                ),
            ),
            (
                "safety",
                lambda item: item["safety"].__setitem__(
                    "calibration_authorized", True
                ),
            ),
            (
                "safety",
                lambda item: item["safety"].__setitem__(
                    "digital_signature_authority", True
                ),
            ),
            (
                "safety",
                lambda item: item["safety"].__setitem__(
                    "trusted_timestamp_authority", True
                ),
            ),
        )
        for expected_error, mutate in mutations:
            with self.subTest(expected_error=expected_error):
                changed = copy.deepcopy(original)
                mutate(changed)
                _write_json(self.evidence["release_path"], changed)
                with self.assertRaisesRegex(
                    ScoringTruthAuthorizationError, expected_error
                ):
                    _verify(self.evidence)

    def test_claimed_release_chronology_is_enforced_but_not_trusted_time(self) -> None:
        self.evidence["release"]["released_at"] = "2026-08-30T03:42:30Z"
        _write_json(self.evidence["release_path"], self.evidence["release"])

        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "chronology"):
            _verify(self.evidence)

        self.assertIs(
            authorization.RELEASE_SAFETY["trusted_timestamp_authority"], False
        )

    def test_duplicate_json_key_and_noncanonical_timestamp_fail_closed(self) -> None:
        original = self.evidence["plan_path"].read_text(encoding="utf-8")
        self.evidence["plan_path"].write_text(
            '{"schema_version":"1.0.0",' + original.lstrip()[1:], encoding="utf-8"
        )
        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "duplicate JSON key"):
            _verify(self.evidence)

        self.evidence["plan_path"].write_bytes(self.evidence["plan_raw"])
        self.evidence["release"]["released_at"] = "2026-08-30T03:44:00.000Z"
        _write_json(self.evidence["release_path"], self.evidence["release"])
        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "not canonical UTC"):
            _verify(self.evidence)

    def test_template_is_deliberately_invalid_and_cannot_execute(self) -> None:
        schema = json.loads(RELEASE_SCHEMA.read_text(encoding="utf-8"))
        template = json.loads(RELEASE_TEMPLATE.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(template)))

        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "template"):
            verify_scoring_truth_event_authorization(
                plan_path=self.evidence["plan_path"],
                release_record_path=RELEASE_TEMPLATE,
                handoff_dir=HANDOFF_DIR,
            )

    def test_manifest_and_artifact_mutation_cannot_bypass_exact_m89(self) -> None:
        for mutation in ("manifest", "artifact"):
            with self.subTest(mutation=mutation):
                copied = self.work_dir / f"handoff-{mutation}"
                shutil.copytree(HANDOFF_DIR, copied)
                manifest_path = copied / "event-handoff-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if mutation == "manifest":
                    manifest["artifacts"][0]["sha256"] = "0" * 64
                    _write_json(manifest_path, manifest)
                else:
                    artifact_path = copied / manifest["artifacts"][0]["path"]
                    artifact_path.write_bytes(artifact_path.read_bytes() + b"\n")

                with self.assertRaisesRegex(
                    ScoringTruthAuthorizationError, "M89 handoff is invalid"
                ):
                    _verify(self.evidence, handoff_dir=copied)

    def test_binding_tamper_and_scope_escalation_are_rejected(self) -> None:
        binding = _verify(self.evidence)
        binding["release_record"]["release_id"] = "fabricated-release"
        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "checksum mismatch"):
            validate_scoring_truth_authorization_binding(binding)

        binding = _verify(self.evidence)
        binding["annotation_workflow_release_only"] = False
        binding["binding_sha256"] = scoring_truth_authorization_binding_sha256(binding)
        with self.assertRaisesRegex(ScoringTruthAuthorizationError, "annotation-only"):
            validate_scoring_truth_authorization_binding(binding)

    def test_crypto_dependency_and_obsolete_contracts_are_removed(self) -> None:
        source = (
            ROOT
            / "src"
            / "rallymate_annotation"
            / "scoring_truth_authorization.py"
        ).read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertNotIn("cryptography", source)
        self.assertNotIn("Ed25519", source)
        self.assertNotIn("cryptography", project)
        self.assertFalse(
            (ROOT / "contracts" / "scoring-truth-external-protocol-receipt.schema.json").exists()
        )
        self.assertFalse(
            (ROOT / "contracts" / "scoring-truth-receipt-trust-anchor.schema.json").exists()
        )
        self.assertFalse(
            (ROOT / "examples" / "scoring-truth-external-protocol-receipt.template.json").exists()
        )
        self.assertFalse(
            (ROOT / "examples" / "scoring-truth-receipt-trust-anchor.template.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
