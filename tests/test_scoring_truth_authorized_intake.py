from __future__ import annotations

"""Synthetic workflow fixtures; never persisted as real authorization or labels."""

import base64
import copy
import csv
import hashlib
import io
import json
import pickle
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

import tests.test_scoring_truth_event_execution as execution_tests
import rallymate_scoring.scoring_truth_authorized_intake as authorized_intake
from tests.test_scoring_truth_intake import _exports, _make_source
from rallymate_annotation.scoring_truth_event_phase_intake import ingest_scoring_truth_event_phase
from rallymate_annotation.scoring_truth_intake import EXPORT_FIELDS, ingest_scoring_truth_exports
from rallymate_scoring.scoring_truth_authorized_intake import (
    LEDGER_VERSION,
    _ATTESTATIONS,
    _INPUT_PATHS,
    validate_scoring_truth_acceptance_ledger,
    verify_scoring_truth_calibration_intake,
)
from rallymate_scoring.scoring_truth_calibration_authorization import (
    CALIBRATION_AUTHORIZATION_BINDING_VERSION,
    CANONICALIZATION,
    VERIFIED_STATUS,
    ScoringTruthCalibrationAuthorizationError,
    require_verified_scoring_truth_calibration_authorization,
    scoring_truth_calibration_authorization_binding_sha256,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _csv(path: Path, rows: list[dict]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS[path.name])
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(stream.getvalue().encode("utf-8"))


class AuthorizedIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Real validators and cross-language A/B/C revision generation, no mocked
        # authorization or bypassed replay. All human-shaped inputs are synthetic.
        fixture = execution_tests.ScoringTruthEventExecutionTests
        fixture.setUpClass()
        cls.fixture = fixture
        cls.root = fixture.root
        node = execution_tests._run_node({
            "boot": fixture.snapshot_c["bootstrap"],
            "A": fixture.submission_a["submission"], "B": fixture.submission_b["submission"],
            "rawA": fixture.submission_a["submission_raw_sha256"],
            "rawB": fixture.submission_b["submission_raw_sha256"],
            "base": fixture.snapshot_c["manifest"]["generated_at"],
        }, r'''
function iso(s) { return new Date(Date.parse(payload.base) + s * 1000).toISOString(); }
const records = {A: {raw_sha256: payload.rawA, submission: payload.A}, B: {raw_sha256: payload.rawB, submission: payload.B}};
const pair = await Core.validateAnnotationPair(payload.boot, [records.A, records.B], "reviewer-C");
const state = Core.createState(payload.boot, "reviewer-C"); state.imports = records;
const video = pair.A.videos[0]; const source = video.events[0];
const id = `m93:C:${video.video_id}:accepted-test-only`;
const event = {event_id: `m93:C:${video.video_id}:final-test-only`, event_code: source.event_code,
  start_ms: source.start_ms, end_ms: source.end_ms, phase_observations: source.phase_observations,
  confidence_milli: source.confidence_milli, boundary_uncertainty_ms: source.boundary_uncertainty_ms,
  notes: "SYNTHETIC SOFTWARE TEST ONLY"};
state.adjudications[id] = Core.saveAdjudicationDraft(null, {
  adjudication_id: id, video_id: video.video_id, decision_status: "accepted_event", event,
  source_annotation_revisions: ["A", "B"].map(slot => ({role_slot: slot,
    annotation_id: pair[slot].videos[0].events[0].annotation_id,
    annotation_revision_sha256: pair[slot].videos[0].events[0].annotation_revision_sha256, relation: "merge_source"})),
  decision_reason: "synthetic accepted fixture",
}, payload.boot, pair, iso(1));
payload.boot.tasks.forEach((task, index) => {
 state.video_adjudications[task.task_id] = Core.saveVideoAdjudicationReview(null,
 {completed: true, notes: "synthetic complete review"}, payload.boot, task, pair, iso(10 + index));
});
const C = await Core.buildAdjudicationSubmission(payload.boot, state, {now: iso(20)});
process.stdout.write(JSON.stringify({raw: Buffer.from(Core.serializeCanonicalFile(C)).toString("base64")}));
''')
        cls.c_path = cls.root / "synthetic-accepted-C.json"
        cls.c_path.write_bytes(base64.b64decode(node["raw"]))
        cls.event_root = cls.root / "event-intake"
        cls.event_manifest = ingest_scoring_truth_event_phase(
            plan_path=fixture.evidence["plan_path"], release_record_path=fixture.evidence["release_path"],
            handoff_dir=execution_tests.HANDOFF_DIR,
            execution_a_bundle_dir=fixture.bundle_a, execution_a_submission_path=fixture.submission_a_path,
            execution_b_bundle_dir=fixture.bundle_b, execution_b_submission_path=fixture.submission_b_path,
            adjudication_bundle_dir=fixture.bundle_c, adjudication_submission_path=cls.c_path,
            output_dir=cls.event_root,
        )
        cls.source = _make_source(cls.root)
        plan = json.loads(fixture.evidence["plan_path"].read_text(encoding="utf-8"))
        pack = json.loads((cls.source / "manifest.json").read_text(encoding="utf-8"))
        pack["videos"] = [{"video_id": row["video_id"], "sha256": row["video_sha256"]} for row in plan["scope"]["tasks"]]
        _write(cls.source / "manifest.json", pack)
        for filename in ("event-annotations.csv", "full-video-review-completion.csv"):
            (cls.source / filename).write_bytes((cls.event_root / "compiled" / filename).read_bytes())
        event = json.loads((cls.event_root / "compiled/manual-events.jsonl").read_text(encoding="utf-8").splitlines()[0])
        cls.event = event
        common = {"video_id": event["video_id"], "event_id": event["event_id"], "indicator_id": "FS01-M03"}
        _csv(cls.source / "semantic-annotations.csv", [{
            **common, "annotation_id": "synthetic-semantic-1", "semantic_key": "bilateral_foot_contact_state",
            "semantic_type": "contact_state", "observable": "false", "null_reason": "synthetic occlusion",
            "annotation_confidence": "0.8", "annotator_id": "synthetic-semantic-author",
            "reviewer_id": "synthetic-semantic-reviewer", "adjudication_status": "accepted",
        }])
        _csv(cls.source / "coach-labels.csv", [
            {**common, "annotation_id": "synthetic-label-" + str(i), "annotator_id": "synthetic-coach-" + str(i), "label_type": "grade", "grade": "B"}
            for i in (1, 2)
        ])
        cls.truth_root = cls.root / "truth-intake"
        truth = ingest_scoring_truth_exports(cls.source, _exports(cls.source), cls.truth_root)
        binding = {
            "binding_version": CALIBRATION_AUTHORIZATION_BINDING_VERSION, "status": VERIFIED_STATUS,
            "canonicalization": CANONICALIZATION, "authorization_id": "synthetic-acceptance-only",
            "event_protocol_authorization": cls.event_manifest["event_authorization"],
            "intake": {k: truth[k] for k in ("intake_id", "intake_version", "content_root_sha256")},
            "revision_lineage": cls.event_manifest["revision_lineage"],
            "input_files": {k: _sha((cls.truth_root / path).read_bytes()) for k, path in _INPUT_PATHS.items()},
        }
        binding["binding_sha256"] = scoring_truth_calibration_authorization_binding_sha256(binding)
        cls.ledger = {
            "ledger_version": LEDGER_VERSION, "ledger_id": "synthetic-ledger", "authority_id": "synthetic-authority",
            "registered_at": "2099-01-02T00:00:00Z", "entries": [{
                "authorization_id": binding["authorization_id"], "status": "active", "scope": "calibration_input_only",
                "current_revision_id": binding["revision_lineage"]["revision_id"],
                "event_intake_manifest_sha256": _sha((cls.event_root / "intake-manifest.json").read_bytes()),
                "binding": binding, "approvals": [
                    {"reviewer_id": "synthetic-acceptance-reviewer-" + str(i), "role": role,
                     "approved_at": "2099-01-01T00:00:00Z", "external_record_sha256": str(i) * 64}
                    for i, role in ((1, "truth_data_reviewer"), (2, "calibration_release_operator"))
                ], "attestations": {key: True for key in _ATTESTATIONS},
                "promotion_authorized": False, "production_scoring_authorized": False,
            }],
        }
        cls.ledger_path = cls.root / "synthetic-acceptance-ledger.json"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.tearDownClass()

    def setUp(self) -> None:
        _write(self.ledger_path, self.ledger)

    def verify(self, **overrides):
        kwargs = dict(event_phase_intake_dir=self.event_root, truth_intake_dir=self.truth_root,
                      trusted_acceptance_ledger_path=self.ledger_path,
                      trusted_acceptance_ledger_sha256=_sha(self.ledger_path.read_bytes()),
                      authorization_id="synthetic-acceptance-only")
        kwargs.update(overrides)
        return verify_scoring_truth_calibration_intake(**kwargs)

    def test_actual_m93_and_csv_replay_issues_only_process_local_binding(self):
        verified = self.verify()
        self.assertEqual(self.ledger["entries"][0]["binding"], require_verified_scoring_truth_calibration_authorization(verified))
        with self.assertRaises(ScoringTruthCalibrationAuthorizationError):
            require_verified_scoring_truth_calibration_authorization(verified.binding)
        with self.assertRaises(TypeError):
            pickle.dumps(verified)
        self.assertFalse(self.ledger["entries"][0]["production_scoring_authorized"])

    def test_wrong_pin_scope_revision_revocation_and_unknown_fields(self):
        with self.assertRaisesRegex(ScoringTruthCalibrationAuthorizationError, "pin mismatch"):
            self.verify(trusted_acceptance_ledger_sha256="0" * 64)
        for key, value in (("scope", "production"), ("current_revision_id", "stale"), ("status", "revoked"), ("production_scoring_authorized", True), ("unknown", True)):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.ledger)
                changed["entries"][0][key] = value
                _write(self.ledger_path, changed)
                with self.assertRaises(ScoringTruthCalibrationAuthorizationError):
                    self.verify()

    def test_ledger_revocation_invalidates_live_object(self):
        verified = self.verify()
        changed = copy.deepcopy(self.ledger)
        changed["entries"][0]["status"] = "revoked"
        _write(self.ledger_path, changed)
        with self.assertRaisesRegex(ScoringTruthCalibrationAuthorizationError, "ledger revoked/changed"):
            require_verified_scoring_truth_calibration_authorization(verified)

    def test_compiled_and_original_csv_tampering_invalidates_live_object(self):
        verified = self.verify()
        for path in (self.truth_root / "compiled/coach-labels.jsonl", self.source / "coach-labels.csv", self.event_root / "raw-submissions/C.json"):
            raw = path.read_bytes()
            try:
                path.write_bytes(raw + b" ")
                with self.subTest(path=path), self.assertRaises(ScoringTruthCalibrationAuthorizationError):
                    require_verified_scoring_truth_calibration_authorization(verified)
            finally:
                path.write_bytes(raw)

    def test_untrusted_nested_ledger_and_duplicate_identity_are_rejected(self):
        with self.assertRaisesRegex(ScoringTruthCalibrationAuthorizationError, "outside"):
            self.verify(trusted_acceptance_ledger_path=self.truth_root / "ledger.json")
        changed = copy.deepcopy(self.ledger)
        changed["entries"][0]["approvals"][1]["reviewer_id"] = "  SYNTHETIC-ACCEPTANCE-REVIEWER-1  "
        _write(self.ledger_path, changed)
        with self.assertRaisesRegex(ScoringTruthCalibrationAuthorizationError, "independent"):
            self.verify()

    def test_duplicate_json_and_nonfinite_are_rejected(self):
        for raw in (b'{"ledger_version": "x", "ledger_version": "y"}', b'{"value":1e9999}', b'{"value":"\\ud800"}'):
            self.ledger_path.write_bytes(raw)
            with self.assertRaises(ScoringTruthCalibrationAuthorizationError):
                self.verify()

    def test_coherently_recompiled_invalid_truth_still_cannot_be_authorized(self):
        for variant in ("empty_labels", "self_review", "same_coach", "different_event"):
            with self.subTest(variant=variant):
                source = self.root / ("source-" + variant)
                shutil.copytree(self.source, source)
                filename = "semantic-annotations.csv" if variant == "self_review" else "coach-labels.csv"
                if variant == "different_event":
                    filename = "event-annotations.csv"
                path = source / filename
                rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
                if variant == "empty_labels":
                    rows = []
                elif variant == "self_review":
                    rows[0]["reviewer_id"] = "  SYNTHETIC-SEMANTIC-AUTHOR  "
                elif variant == "same_coach":
                    rows[1]["annotator_id"] = "  SYNTHETIC-COACH-1  "
                else:
                    rows[0]["start_ms"] = "999"
                _csv(path, rows)
                truth_root = self.root / ("truth-" + variant)
                truth = ingest_scoring_truth_exports(source, _exports(source), truth_root)
                ledger = copy.deepcopy(self.ledger)
                binding = ledger["entries"][0]["binding"]
                binding["intake"] = {k: truth[k] for k in ("intake_id", "intake_version", "content_root_sha256")}
                binding["input_files"] = {k: _sha((truth_root / path).read_bytes()) for k, path in _INPUT_PATHS.items()}
                binding["binding_sha256"] = scoring_truth_calibration_authorization_binding_sha256(binding)
                _write(self.ledger_path, ledger)
                with self.assertRaises(ScoringTruthCalibrationAuthorizationError):
                    self.verify(truth_intake_dir=truth_root)
                if variant == "same_coach":
                    # Before the fix, a second read could supply different valid
                    # identities while the ledger still bound the duplicate ones.
                    original_read = authorized_intake._read
                    good_labels = (self.truth_root / "compiled/coach-labels.jsonl").read_bytes()
                    reads = 0

                    def alternating_labels(root, relative):
                        nonlocal reads
                        if root == truth_root and relative == "compiled/coach-labels.jsonl":
                            reads += 1
                            if reads % 2 == 0:
                                return good_labels
                        return original_read(root, relative)

                    with patch.object(authorized_intake, "_read", side_effect=alternating_labels):
                        with self.assertRaises(ScoringTruthCalibrationAuthorizationError):
                            self.verify(truth_intake_dir=truth_root)

    def test_transient_projection_and_plan_bytes_cannot_differ_from_final_snapshot(self):
        original_read = authorized_intake._read

        for target in ("projection", "plan"):
            def transient_read(root, relative):
                if target == "projection" and relative in {"event-annotations.csv", "compiled/event-annotations.csv"}:
                    return b"same transient forged projection on both sides"
                if target == "plan" and relative == "authority/plan.json":
                    return original_read(root, relative) + b" "
                return original_read(root, relative)

            with self.subTest(target=target), patch.object(authorized_intake, "_read", side_effect=transient_read):
                with self.assertRaisesRegex(ScoringTruthCalibrationAuthorizationError, "changed after semantic checks"):
                    self.verify()

    def test_approval_predating_evidence_and_collector_approval_are_rejected(self):
        for changes in ({"approved_at": "2000-01-01T00:00:00Z"}, {"reviewer_id": "synthetic-coach-1"}):
            ledger = copy.deepcopy(self.ledger)
            ledger["entries"][0]["approvals"][0].update(changes)
            _write(self.ledger_path, ledger)
            with self.subTest(changes=changes), self.assertRaises(ScoringTruthCalibrationAuthorizationError):
                self.verify()

    def test_closed_schema_and_runtime_validator_agree(self):
        schema = json.loads((execution_tests.ROOT / "contracts/scoring-truth-calibration-acceptance-ledger.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(self.ledger)
        validate_scoring_truth_acceptance_ledger(self.ledger)
        invalid = copy.deepcopy(self.ledger)
        invalid["entries"][0]["attestations"]["invented"] = True
        self.assertTrue(list(validator.iter_errors(invalid)))
        with self.assertRaises(ScoringTruthCalibrationAuthorizationError):
            validate_scoring_truth_acceptance_ledger(invalid)


if __name__ == "__main__":
    unittest.main()
