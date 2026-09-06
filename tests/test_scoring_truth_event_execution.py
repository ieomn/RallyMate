from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

import rallymate_annotation.scoring_truth_event_execution as execution
from rallymate_annotation.scoring_truth_event_execution import (
    ADJUDICATION_BUNDLE_STATUS,
    ADJUDICATION_MANIFEST_NAME,
    EXECUTION_BUNDLE_STATUS,
    EXECUTION_MANIFEST_NAME,
    ScoringTruthEventExecutionError,
    build_scoring_truth_event_adjudication_bundle,
    build_scoring_truth_event_execution_bundle,
    validate_scoring_truth_event_adjudication_bundle,
    validate_scoring_truth_event_adjudication_submission,
    validate_scoring_truth_event_annotation_submission,
    validate_scoring_truth_event_execution_bundle,
)
from tests.test_scoring_truth_authorization import HANDOFF_DIR, _make_evidence


ROOT = Path(__file__).resolve().parents[1]
CORE = (
    ROOT
    / "src"
    / "rallymate_annotation"
    / "assets"
    / "scoring-truth-event-collection-core.js"
)
HTML_TEMPLATE = (
    ROOT
    / "src"
    / "rallymate_annotation"
    / "assets"
    / "scoring-truth-event-collection-workbench.html"
)
SCHEMAS = [
    ROOT / "contracts" / "scoring-truth-event-execution-bundle.schema.json",
    ROOT / "contracts" / "scoring-truth-event-annotation-submission.schema.json",
    ROOT / "contracts" / "scoring-truth-event-adjudication-bundle.schema.json",
    ROOT / "contracts" / "scoring-truth-event-adjudication-submission.schema.json",
]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_file(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _milliseconds_after(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00") + timedelta(seconds=seconds)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _recompute_annotation_submission_revisions(
    submission: dict, manifest: dict
) -> None:
    bundle_ref = {
        "bundle_id": manifest["bundle_id"],
        "manifest_binding_sha256": manifest["manifest_binding_sha256"],
    }
    lineage = {
        "execution_id": manifest["execution_id"],
        "execution_bundle": bundle_ref,
        "authorization_binding_sha256": manifest["event_authorization"][
            "binding_sha256"
        ],
        "role_slot": submission["role_slot"],
        "annotator_id": submission["annotator_id"],
    }
    tasks = manifest["scope"]["tasks"]
    for video, task in zip(submission["videos"], tasks):
        review = video["full_video_review"]
        review_base = {
            "completed": review["completed"],
            "notes": review["notes"],
            "reviewed_at": review["reviewed_at"],
        }
        review["review_revision_sha256"] = _sha256(
            _canonical_file(
                {
                    **lineage,
                    "task_id": task["task_id"],
                    "video_id": task["video_id"],
                    "full_video_review": review_base,
                }
            )[:-1]
        )
        video_base = {
            "task_id": video["task_id"],
            "video_id": video["video_id"],
            "full_video_review": video["full_video_review"],
            "events": video["events"],
        }
        video["video_revision_sha256"] = _sha256(
            _canonical_file({**lineage, **video_base})[:-1]
        )
    revision_payload = {
        key: value
        for key, value in submission.items()
        if key not in {"submission_revision_sha256", "exported_at"}
    }
    submission["submission_revision_sha256"] = _sha256(
        _canonical_file(revision_payload)[:-1]
    )


def _run_node(payload: dict, source: str) -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is unavailable")
    script = f"""
const Core = require({json.dumps(str(CORE))});
const payload = JSON.parse(Buffer.from(process.argv[1], "base64").toString("utf8"));
(async () => {{
{source}
}})().catch((error) => {{ console.error(error && error.stack || error); process.exit(1); }});
"""
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    completed = subprocess.run(
        [node, "-e", script, encoded],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


class ScoringTruthEventExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory(prefix="rallymate-m93-execution-")
        cls.root = Path(cls._temp.name)
        release_dir = cls.root / "synthetic-release"
        release_dir.mkdir()
        cls.evidence = _make_evidence(cls.root, release_dir)
        cls.bundle_a = cls.root / "execution-A"
        cls.bundle_b = cls.root / "execution-B"
        build_scoring_truth_event_execution_bundle(
            plan_path=cls.evidence["plan_path"],
            release_record_path=cls.evidence["release_path"],
            handoff_dir=HANDOFF_DIR,
            role_slot="A",
            output_dir=cls.bundle_a,
        )
        build_scoring_truth_event_execution_bundle(
            plan_path=cls.evidence["plan_path"],
            release_record_path=cls.evidence["release_path"],
            handoff_dir=HANDOFF_DIR,
            role_slot="B",
            output_dir=cls.bundle_b,
        )
        cls.snapshot_a = validate_scoring_truth_event_execution_bundle(
            cls.bundle_a, expected_role_slot="A"
        )
        cls.snapshot_b = validate_scoring_truth_event_execution_bundle(
            cls.bundle_b, expected_role_slot="B"
        )
        base = max(
            cls.snapshot_a["manifest"]["generated_at"],
            cls.snapshot_b["manifest"]["generated_at"],
        )
        node_result = _run_node(
            {
                "bootA": cls.snapshot_a["bootstrap"],
                "bootB": cls.snapshot_b["bootstrap"],
                "base": base,
            },
            r"""
function iso(seconds) {
  return new Date(Date.parse(payload.base) + seconds * 1000).toISOString();
}
function phases(start) {
  return {
    preload_ms: {status: "observed", timestamp_ms: start + 100, reason: ""},
    takeoff_proxy_ms: {status: "observed", timestamp_ms: start + 200, reason: ""},
    landing_proxy_ms: {status: "unobservable", timestamp_ms: null, reason: "occluded"},
    redistribution_ms: {status: "observed", timestamp_ms: start + 700, reason: ""},
    initiation_ms: {status: "observed", timestamp_ms: start + 900, reason: ""},
  };
}
async function build(boot, participant, offset) {
  const state = Core.createState(boot, participant);
  const first = boot.tasks[0];
  const event = {
    annotation_id: `m93:${boot.role_slot}:${first.video_id}:event-0001`,
    event_code: "FS01", start_ms: 1000, end_ms: 2200,
    phase_observations: phases(1000), confidence_milli: 900,
    boundary_uncertainty_ms: 50, notes: "",
  };
  state.videos[first.task_id].events[event.annotation_id] = Core.saveEventDraft(
    null, event, boot, first, iso(offset + 1),
  );
  boot.tasks.forEach((task, index) => {
    state.videos[task.task_id].full_video_review = Core.saveReviewDraft(
      null, {completed: true, notes: index === 0 ? "event reviewed" : "confirmed zero events"},
      iso(offset + 10 + index),
    );
  });
  return Core.buildAnnotationSubmission(boot, state, {now: iso(offset + 20)});
}
const A = await build(payload.bootA, "annotator-A", 1);
const B = await build(payload.bootB, "annotator-B", 2);
const rawA = Core.serializeCanonicalFile(A);
const rawB = Core.serializeCanonicalFile(B);
process.stdout.write(JSON.stringify({A: Buffer.from(rawA).toString("base64"), B: Buffer.from(rawB).toString("base64")}));
""",
        )
        cls.submission_a_path = cls.root / "annotator-A.json"
        cls.submission_b_path = cls.root / "annotator-B.json"
        cls.submission_a_path.write_bytes(base64.b64decode(node_result["A"]))
        cls.submission_b_path.write_bytes(base64.b64decode(node_result["B"]))
        cls.submission_a = validate_scoring_truth_event_annotation_submission(
            cls.submission_a_path, cls.bundle_a
        )
        cls.submission_b = validate_scoring_truth_event_annotation_submission(
            cls.submission_b_path, cls.bundle_b
        )
        cls.bundle_c = cls.root / "adjudication-C"
        build_scoring_truth_event_adjudication_bundle(
            annotator_a_bundle_dir=cls.bundle_a,
            annotator_a_submission_path=cls.submission_a_path,
            annotator_b_bundle_dir=cls.bundle_b,
            annotator_b_submission_path=cls.submission_b_path,
            output_dir=cls.bundle_c,
        )
        cls.snapshot_c = validate_scoring_truth_event_adjudication_bundle(cls.bundle_c)
        c_base = cls.snapshot_c["manifest"]["generated_at"]
        node_c = _run_node(
            {
                "boot": cls.snapshot_c["bootstrap"],
                "A": cls.submission_a["submission"],
                "B": cls.submission_b["submission"],
                "rawA": cls.submission_a["submission_raw_sha256"],
                "rawB": cls.submission_b["submission_raw_sha256"],
                "base": c_base,
            },
            r"""
function iso(seconds) { return new Date(Date.parse(payload.base) + seconds * 1000).toISOString(); }
const records = {
  A: {raw_sha256: payload.rawA, submission: payload.A},
  B: {raw_sha256: payload.rawB, submission: payload.B},
};
const pair = await Core.validateAnnotationPair(payload.boot, [records.A, records.B], "reviewer-C");
const state = Core.createState(payload.boot, "reviewer-C");
state.imports = records;
for (const slot of ["A", "B"]) {
  const video = pair[slot].videos[0];
  const source = video.events[0];
  const id = `m93:C:${video.video_id}:reject-${slot}`;
  state.adjudications[id] = Core.saveAdjudicationDraft(null, {
    adjudication_id: id, video_id: video.video_id,
    decision_status: "rejected_sources",
    source_annotation_revisions: [{role_slot: slot, annotation_id: source.annotation_id, annotation_revision_sha256: source.annotation_revision_sha256, relation: "rejected_source"}],
    event: null, decision_reason: "reviewed and rejected",
  }, payload.boot, pair, iso(slot === "A" ? 1 : 2));
}
payload.boot.tasks.forEach((task, index) => {
  state.video_adjudications[task.task_id] = Core.saveVideoAdjudicationReview(
    null, {completed: true, notes: "C full-video review complete"},
    payload.boot, task, pair, iso(10 + index),
  );
});
const C = await Core.buildAdjudicationSubmission(payload.boot, state, {now: iso(20)});
process.stdout.write(JSON.stringify({C: Buffer.from(Core.serializeCanonicalFile(C)).toString("base64")}));
""",
        )
        cls.submission_c_path = cls.root / "reviewer-C.json"
        cls.submission_c_path.write_bytes(base64.b64decode(node_c["C"]))
        cls.submission_c = validate_scoring_truth_event_adjudication_submission(
            cls.submission_c_path, cls.bundle_c
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def _copytree(self, source: Path, name: str) -> Path:
        target = self.root / name
        shutil.copytree(source, target, copy_function=os.link)
        return target

    def _rewrite_declared_artifact(
        self,
        bundle: Path,
        manifest_name: str,
        artifact_path: str,
        raw: bytes,
    ) -> None:
        target = bundle / Path(*artifact_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        target.write_bytes(raw)
        manifest_path = bundle / manifest_name
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = next(
            (item for item in manifest["artifacts"] if item["path"] == artifact_path),
            None,
        )
        updated = {
            "path": artifact_path,
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }
        if record is None:
            manifest["artifacts"].append(updated)
            manifest["artifacts"].sort(key=lambda item: item["path"])
        else:
            record.update(updated)
        manifest["content_root_sha256"] = _sha256(
            _canonical_file({"artifacts": manifest["artifacts"]})[:-1]
        )
        manifest_path.unlink()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_cross_language_end_to_end_and_role_topology(self) -> None:
        self.assertEqual(EXECUTION_BUNDLE_STATUS, self.snapshot_a["manifest"]["status"])
        self.assertEqual("A", self.snapshot_a["role_slot"])
        self.assertEqual("B", self.snapshot_b["role_slot"])
        self.assertEqual(self.snapshot_a["execution_id"], self.snapshot_b["execution_id"])
        self.assertEqual(ADJUDICATION_BUNDLE_STATUS, self.snapshot_c["manifest"]["status"])
        self.assertEqual("C", self.submission_c["role_slot"])
        self.assertEqual(3, len(self.submission_c["submission"]["video_adjudications"]))
        for slot, submission in (("A", self.submission_a), ("B", self.submission_b)):
            self.assertEqual(slot, submission["role_slot"])
            self.assertEqual(3, len(submission["submission"]["videos"]))
            self.assertEqual(1, sum(len(video["events"]) for video in submission["submission"]["videos"]))
            phase = submission["submission"]["videos"][0]["events"][0]["phase_observations"]["landing_proxy_ms"]
            self.assertEqual({"status": "unobservable", "timestamp_ms": None, "reason": "occluded"}, phase)

    def test_cross_language_same_start_ids_use_codepoint_order(self) -> None:
        result = _run_node(
            {
                "bootA": self.snapshot_a["bootstrap"],
                "bootC": self.snapshot_c["bootstrap"],
                "sourceA": self.submission_a["submission"],
                "sourceB": self.submission_b["submission"],
                "rawA": self.submission_a["submission_raw_sha256"],
                "rawB": self.submission_b["submission_raw_sha256"],
                "baseA": self.snapshot_a["manifest"]["generated_at"],
                "baseC": self.snapshot_c["manifest"]["generated_at"],
            },
            r"""
function iso(base, seconds) {
  return new Date(Date.parse(base) + seconds * 1000).toISOString();
}
const stateA = Core.createState(payload.bootA, "sort-A");
payload.bootA.tasks.forEach((task, index) => {
  stateA.videos[task.task_id].full_video_review = Core.saveReviewDraft(
    null, {completed: true, notes: "codepoint order review"},
    iso(payload.baseA, 10 + index),
  );
});
const sourceDecision = payload.sourceA.videos[0].events[0];
for (const suffix of ["_x", "-x"]) {
  const decision = Object.fromEntries([
    "annotation_id", "event_code", "start_ms", "end_ms", "phase_observations",
    "confidence_milli", "boundary_uncertainty_ms", "notes",
  ].map((key) => [key, sourceDecision[key]]));
  decision.annotation_id = `m93:A:${payload.bootA.tasks[0].video_id}:${suffix}`;
  stateA.videos[payload.bootA.tasks[0].task_id].events[decision.annotation_id] = Core.saveEventDraft(
    null, decision, payload.bootA, payload.bootA.tasks[0], iso(payload.baseA, 1),
  );
}
const sortedA = await Core.buildAnnotationSubmission(
  payload.bootA, stateA, {now: iso(payload.baseA, 20)},
);

const records = {
  A: {raw_sha256: payload.rawA, submission: payload.sourceA},
  B: {raw_sha256: payload.rawB, submission: payload.sourceB},
};
const pair = await Core.validateAnnotationPair(payload.bootC, [records.A, records.B], "sort-C");
const stateC = Core.createState(payload.bootC, "sort-C");
stateC.imports = records;
for (const [slot, suffix] of [["A", "_x"], ["B", "-x"]]) {
  const source = pair[slot].videos[0].events[0];
  const id = `m93:C:${pair[slot].videos[0].video_id}:${suffix}`;
  stateC.adjudications[id] = Core.saveAdjudicationDraft(null, {
    adjudication_id: id,
    video_id: pair[slot].videos[0].video_id,
    decision_status: "rejected_sources",
    source_annotation_revisions: [{
      role_slot: slot,
      annotation_id: source.annotation_id,
      annotation_revision_sha256: source.annotation_revision_sha256,
      relation: "rejected_source",
    }],
    event: null,
    decision_reason: "codepoint order rejection",
  }, payload.bootC, pair, iso(payload.baseC, 1));
}
payload.bootC.tasks.forEach((task, index) => {
  stateC.video_adjudications[task.task_id] = Core.saveVideoAdjudicationReview(
    null, {completed: true, notes: "codepoint order review"},
    payload.bootC, task, pair, iso(payload.baseC, 10 + index),
  );
});
const sortedC = await Core.buildAdjudicationSubmission(
  payload.bootC, stateC, {now: iso(payload.baseC, 20)},
);
process.stdout.write(JSON.stringify({
  rawA: Buffer.from(Core.serializeCanonicalFile(sortedA)).toString("base64"),
  rawC: Buffer.from(Core.serializeCanonicalFile(sortedC)).toString("base64"),
  orderA: sortedA.videos[0].events.map((row) => row.annotation_id),
  orderC: sortedC.decisions.map((row) => row.adjudication_id),
}));
""",
        )
        self.assertTrue(result["orderA"][0].endswith(":-x"))
        self.assertTrue(result["orderA"][1].endswith(":_x"))
        self.assertTrue(result["orderC"][0].endswith(":-x"))
        self.assertTrue(result["orderC"][1].endswith(":_x"))
        annotation_path = self.root / "codepoint-sort-A.json"
        annotation_path.write_bytes(base64.b64decode(result["rawA"]))
        validate_scoring_truth_event_annotation_submission(
            annotation_path, self.bundle_a
        )
        adjudication_path = self.root / "codepoint-sort-C.json"
        adjudication_path.write_bytes(base64.b64decode(result["rawC"]))
        validate_scoring_truth_event_adjudication_submission(
            adjudication_path, self.bundle_c
        )

    def test_schema_instances_and_exact_versions(self) -> None:
        instances = (
            self.snapshot_a["manifest"],
            self.submission_a["submission"],
            self.snapshot_c["manifest"],
            self.submission_c["submission"],
        )
        for schema_path, instance in zip(SCHEMAS, instances):
            with self.subTest(schema=schema_path.name):
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).validate(instance)

    def test_canonical_bytes_role_and_revision_tamper_fail_closed(self) -> None:
        pretty = self.root / "pretty-A.json"
        pretty.write_text(
            json.dumps(self.submission_a["submission"], indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError, "canonical UTF-8 JSON"
        ):
            validate_scoring_truth_event_annotation_submission(pretty, self.bundle_a)
        changed = copy.deepcopy(self.submission_a["submission"])
        changed["videos"][0]["events"][0]["confidence_milli"] = 899
        changed_path = self.root / "changed-A.json"
        changed_path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "revision mismatch"):
            validate_scoring_truth_event_annotation_submission(changed_path, self.bundle_a)
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "role"):
            validate_scoring_truth_event_annotation_submission(
                self.submission_a_path, self.bundle_b
            )

    def test_strict_json_duplicate_nonfinite_surrogate_and_timestamp_fail_closed(self) -> None:
        malformed = (
            (b'{"schema_version":"1.0.0","schema_version":"1.0.0"}\n', "duplicate JSON key"),
            (b'{"value":NaN}\n', "invalid JSON constant"),
            (b'{"value":"\\ud800"}\n', "isolated surrogate"),
        )
        for index, (raw, message) in enumerate(malformed):
            with self.subTest(message=message):
                path = self.root / f"malformed-{index}.json"
                path.write_bytes(raw)
                with self.assertRaisesRegex(ScoringTruthEventExecutionError, message):
                    validate_scoring_truth_event_annotation_submission(path, self.bundle_a)
        changed = copy.deepcopy(self.submission_a["submission"])
        changed["submitted_at"] = changed["submitted_at"].replace("Z", "000Z")
        path = self.root / "microsecond-submission.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError, "canonical millisecond UTC"
        ):
            validate_scoring_truth_event_annotation_submission(path, self.bundle_a)

    def test_cross_language_integers_are_javascript_safe(self) -> None:
        changed = copy.deepcopy(self.submission_a["submission"])
        changed["videos"][0]["events"][0]["boundary_uncertainty_ms"] = (
            9_007_199_254_740_993
        )
        raw = _canonical_file(changed)
        path = self.root / "unsafe-integer-A.json"
        path.write_bytes(raw)
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError, "JavaScript safe integer range"
        ):
            validate_scoring_truth_event_annotation_submission(path, self.bundle_a)
        schema = json.loads(SCHEMAS[1].read_text(encoding="utf-8"))
        self.assertFalse(Draft202012Validator(schema).is_valid(changed))
        node = _run_node(
            {"raw": base64.b64encode(raw).decode("ascii")},
            r"""
let rejected = false;
try {
  Core.parseCanonicalJsonBytes(Uint8Array.from(Buffer.from(payload.raw, "base64")));
} catch (_error) {
  rejected = true;
}
process.stdout.write(JSON.stringify({rejected}));
""",
        )
        self.assertTrue(node["rejected"])

    def test_phase_missing_is_not_zero_and_duration_bounds_are_enforced(self) -> None:
        changed = copy.deepcopy(self.submission_a["submission"])
        event = changed["videos"][0]["events"][0]
        event["phase_observations"]["landing_proxy_ms"] = {
            "status": "observed",
            "timestamp_ms": 0,
            "reason": "",
        }
        path = self.root / "missing-as-zero.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "below its minimum"):
            validate_scoring_truth_event_annotation_submission(path, self.bundle_a)
        changed = copy.deepcopy(self.submission_a["submission"])
        changed["videos"][0]["events"][0]["end_ms"] = (
            self.snapshot_a["manifest"]["scope"]["tasks"][0]["duration_ms"] + 1
        )
        path = self.root / "past-duration.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "exceeds its maximum"):
            validate_scoring_truth_event_annotation_submission(path, self.bundle_a)

    def test_annotation_and_adjudication_claimed_chronology_fail_closed(self) -> None:
        changed = copy.deepcopy(self.submission_a["submission"])
        event = changed["videos"][0]["events"][0]
        event["annotated_at"] = _milliseconds_after(
            changed["videos"][0]["full_video_review"]["reviewed_at"], 1
        )
        path = self.root / "late-annotation.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "chronology"):
            validate_scoring_truth_event_annotation_submission(path, self.bundle_a)
        changed_c = copy.deepcopy(self.submission_c["submission"])
        changed_c["video_adjudications"][0]["adjudicated_at"] = _milliseconds_after(
            changed_c["adjudicated_at"], 1
        )
        path_c = self.root / "late-C-review.json"
        path_c.write_bytes(_canonical_file(changed_c))
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "chronology"):
            validate_scoring_truth_event_adjudication_submission(path_c, self.bundle_c)

    def test_c_decision_cannot_postdate_same_video_review(self) -> None:
        changed = copy.deepcopy(self.submission_c["submission"])
        decision = changed["decisions"][0]
        review = next(
            item
            for item in changed["video_adjudications"]
            if item["video_id"] == decision["video_id"]
        )
        decision["adjudicated_at"] = _milliseconds_after(
            review["adjudicated_at"], 1
        )
        decision_base = {
            key: decision[key]
            for key in (
                "adjudication_id",
                "video_id",
                "decision_status",
                "source_video_revisions",
                "source_annotation_revisions",
                "event",
                "decision_reason",
                "adjudicated_at",
            )
        }
        manifest = self.snapshot_c["manifest"]
        decision["adjudication_revision_sha256"] = _sha256(
            _canonical_file(
                {
                    "execution_id": manifest["execution_id"],
                    "adjudication_bundle": {
                        "bundle_id": manifest["bundle_id"],
                        "manifest_binding_sha256": manifest[
                            "manifest_binding_sha256"
                        ],
                    },
                    "authorization_binding_sha256": manifest[
                        "event_authorization"
                    ]["binding_sha256"],
                    "reviewer_slot": "C",
                    "reviewer_id": changed["reviewer_id"],
                    "decision": decision_base,
                }
            )[:-1]
        )
        revision_payload = {
            key: value
            for key, value in changed.items()
            if key
            not in {
                "adjudication_submission_revision_sha256",
                "exported_at",
            }
        }
        changed["adjudication_submission_revision_sha256"] = _sha256(
            _canonical_file(revision_payload)[:-1]
        )
        path = self.root / "decision-after-video-review.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError,
            "decision/video-review chronology",
        ):
            validate_scoring_truth_event_adjudication_submission(path, self.bundle_c)

    def test_zero_event_video_review_cannot_postdate_submission(self) -> None:
        changed = copy.deepcopy(self.submission_a["submission"])
        self.assertEqual([], changed["videos"][1]["events"])
        changed["videos"][1]["full_video_review"]["reviewed_at"] = _milliseconds_after(
            changed["exported_at"], 60
        )
        _recompute_annotation_submission_revisions(
            changed, self.snapshot_a["manifest"]
        )
        path = self.root / "late-zero-event-review.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError, "full-video review chronology"
        ):
            validate_scoring_truth_event_annotation_submission(path, self.bundle_a)

    def test_c_source_coverage_and_exact_revision_binding_fail_closed(self) -> None:
        changed = copy.deepcopy(self.submission_c["submission"])
        changed["decisions"].pop()
        path = self.root / "missing-C-source.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "coverage"):
            validate_scoring_truth_event_adjudication_submission(path, self.bundle_c)
        changed = copy.deepcopy(self.submission_c["submission"])
        changed["decisions"][0]["source_annotation_revisions"][0][
            "annotation_revision_sha256"
        ] = "0" * 64
        path = self.root / "drifted-C-source.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError, "source annotation revision mismatch"
        ):
            validate_scoring_truth_event_adjudication_submission(path, self.bundle_c)

    def test_bundle_tamper_extra_file_and_portable_validation_fail_closed(self) -> None:
        copied = self._copytree(self.bundle_a, "tampered-A")
        (copied / "extra.txt").write_text("not declared", encoding="utf-8")
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "topology"):
            validate_scoring_truth_event_execution_bundle(copied)
        completed = subprocess.run(
            [
                sys.executable,
                os.fspath(self.bundle_c / "serve_scoring_truth_event_execution.py"),
                "--directory",
                os.fspath(self.bundle_c),
                "--validate-only",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["ok"])

    def test_portable_server_dynamic_port_and_read_only_methods(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                os.fspath(self.bundle_a / "serve_scoring_truth_event_execution.py"),
                "--directory",
                os.fspath(self.bundle_a),
                "--port",
                "0",
                "--no-open",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        connection: http.client.HTTPConnection | None = None
        try:
            assert process.stdout is not None
            startup = json.loads(process.stdout.readline())
            self.assertTrue(startup["ok"])
            parsed = urlsplit(startup["url"])
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            self.assertTrue(response.read())
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"):
                with self.subTest(method=method):
                    connection.request(method, "/")
                    response = connection.getresponse()
                    self.assertEqual(405, response.status)
                    self.assertEqual("GET, HEAD", response.getheader("Allow"))
                    self.assertEqual(b"", response.read())
        finally:
            if connection is not None:
                connection.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def test_adjudication_bundle_cannot_predate_source_exports(self) -> None:
        copied = self._copytree(self.bundle_c, "early-adjudication-bundle")
        manifest_path = copied / ADJUDICATION_MANIFEST_NAME
        manifest_path.unlink()
        manifest = copy.deepcopy(self.snapshot_c["manifest"])
        manifest["annotation_submissions"][0]["exported_at"] = _milliseconds_after(
            manifest["generated_at"], 60
        )
        projection = {
            key: value
            for key, value in manifest.items()
            if key
            not in {
                "artifacts",
                "content_root_sha256",
                "manifest_binding_sha256",
            }
        }
        manifest["manifest_binding_sha256"] = _sha256(
            _canonical_file(projection)[:-1]
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._rewrite_declared_artifact(
            copied,
            ADJUDICATION_MANIFEST_NAME,
            "review.html",
            execution._inject_bootstrap(
                HTML_TEMPLATE.read_bytes(), execution._adjudication_bootstrap(manifest)
            ),
        )
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError,
            "generated_at predates a source submission export",
        ):
            validate_scoring_truth_event_adjudication_bundle(copied)

    def test_adjudication_scope_frame_rate_must_equal_m89(self) -> None:
        copied = self._copytree(self.bundle_c, "drifted-c-frame-rate")
        manifest_path = copied / ADJUDICATION_MANIFEST_NAME
        manifest_path.unlink()
        manifest = copy.deepcopy(self.snapshot_c["manifest"])
        manifest["scope"]["tasks"][0]["frame_rate"] = {
            "numerator": 29979,
            "denominator": 1000,
        }
        projection = {
            key: value
            for key, value in manifest.items()
            if key
            not in {
                "artifacts",
                "content_root_sha256",
                "manifest_binding_sha256",
            }
        }
        manifest["manifest_binding_sha256"] = _sha256(
            _canonical_file(projection)[:-1]
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._rewrite_declared_artifact(
            copied,
            ADJUDICATION_MANIFEST_NAME,
            "review.html",
            execution._inject_bootstrap(
                HTML_TEMPLATE.read_bytes(), execution._adjudication_bootstrap(manifest)
            ),
        )
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError, r"scope.tasks\[0\].frame_rate"
        ):
            validate_scoring_truth_event_adjudication_bundle(copied)

    def test_execution_bundle_cannot_predate_future_claimed_release(self) -> None:
        evidence_root = self.root / "future-release-evidence"
        evidence_root.mkdir()
        release_root = evidence_root / "release"
        release_root.mkdir()
        evidence = _make_evidence(evidence_root, release_root)
        release = copy.deepcopy(evidence["release"])
        release["released_at"] = "9998-01-01T00:00:00Z"
        evidence["release_path"].write_text(
            json.dumps(release, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        bundle = self.root / "future-release-execution-A"
        built = build_scoring_truth_event_execution_bundle(
            plan_path=evidence["plan_path"],
            release_record_path=evidence["release_path"],
            handoff_dir=HANDOFF_DIR,
            role_slot="A",
            output_dir=bundle,
        )
        self.assertEqual(
            release["released_at"], built["generated_at"]
        )

        manifest_path = bundle / EXECUTION_MANIFEST_NAME
        manifest = copy.deepcopy(built)
        manifest["generated_at"] = "2026-09-02T00:00:00Z"
        projection = {
            key: value
            for key, value in manifest.items()
            if key
            not in {
                "artifacts",
                "content_root_sha256",
                "manifest_binding_sha256",
            }
        }
        manifest["manifest_binding_sha256"] = _sha256(
            _canonical_file(projection)[:-1]
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._rewrite_declared_artifact(
            bundle,
            EXECUTION_MANIFEST_NAME,
            "review.html",
            execution._inject_bootstrap(
                HTML_TEMPLATE.read_bytes(), execution._execution_bootstrap(manifest)
            ),
        )
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError,
            "generated_at predates the operator release",
        ):
            validate_scoring_truth_event_execution_bundle(bundle)

    def test_coordinated_asset_rehash_cannot_replace_approved_ui(self) -> None:
        copied = self._copytree(self.bundle_a, "coordinated-ui-tamper")
        core_path = copied / "scoring-truth-event-collection-core.js"
        core_path.unlink()
        core_path.write_bytes(core_path.read_bytes() + b"\n" if core_path.exists() else self.snapshot_a["artifacts"]["scoring-truth-event-collection-core.js"]["raw"] + b"\n")
        manifest_path = copied / EXECUTION_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = next(
            item
            for item in manifest["artifacts"]
            if item["path"] == "scoring-truth-event-collection-core.js"
        )
        raw = core_path.read_bytes()
        record["bytes"] = len(raw)
        record["sha256"] = _sha256(raw)
        manifest["content_root_sha256"] = _sha256(
            json.dumps(
                {"artifacts": manifest["artifacts"]},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        manifest_path.unlink()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ScoringTruthEventExecutionError, "approved workbench asset drifted"
        ):
            validate_scoring_truth_event_execution_bundle(copied)

    def test_review_template_and_bootstrap_are_exact_in_portable_validator(self) -> None:
        original = self.snapshot_a["artifacts"]["review.html"]["raw"]
        changed_id = "m93-event-execution-" + "0" * 64
        cases = (
            (
                "template",
                original.replace(b"RallyMate", b"RallyMote", 1),
                "approved workbench HTML template drifted",
            ),
            (
                "bootstrap",
                original.replace(
                    self.snapshot_a["execution_id"].encode("ascii"),
                    changed_id.encode("ascii"),
                    1,
                ),
                "review.html bootstrap does not match manifest",
            ),
        )
        for name, raw, message in cases:
            with self.subTest(name=name):
                self.assertNotEqual(original, raw)
                copied = self._copytree(self.bundle_a, f"drifted-review-{name}")
                self._rewrite_declared_artifact(
                    copied, EXECUTION_MANIFEST_NAME, "review.html", raw
                )
                with self.assertRaisesRegex(
                    ScoringTruthEventExecutionError, message
                ):
                    validate_scoring_truth_event_execution_bundle(copied)
                completed = subprocess.run(
                    [
                        sys.executable,
                        os.fspath(
                            copied / "serve_scoring_truth_event_execution.py"
                        ),
                        "--directory",
                        os.fspath(copied),
                        "--validate-only",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(2, completed.returncode)
                self.assertIn(message, completed.stderr)

    def test_declared_extra_artifacts_fail_exact_a_and_c_topology(self) -> None:
        for source, manifest_name, validator, label in (
            (
                self.bundle_a,
                EXECUTION_MANIFEST_NAME,
                validate_scoring_truth_event_execution_bundle,
                "A",
            ),
            (
                self.bundle_c,
                ADJUDICATION_MANIFEST_NAME,
                validate_scoring_truth_event_adjudication_bundle,
                "C",
            ),
        ):
            with self.subTest(role=label):
                copied = self._copytree(source, f"declared-extra-{label}")
                self._rewrite_declared_artifact(
                    copied, manifest_name, "notes.txt", b"declared but forbidden\n"
                )
                with self.assertRaisesRegex(
                    ScoringTruthEventExecutionError, "artifact topology"
                ):
                    validator(copied)
                completed = subprocess.run(
                    [
                        sys.executable,
                        os.fspath(
                            copied / "serve_scoring_truth_event_execution.py"
                        ),
                        "--directory",
                        os.fspath(copied),
                        "--validate-only",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(2, completed.returncode)
                self.assertIn("artifact topology", completed.stderr)

    def test_c_source_and_reviewer_identity_tamper_fail_closed(self) -> None:
        changed = copy.deepcopy(self.submission_c["submission"])
        changed["reviewer_id"] = self.submission_a["annotator_id"]
        path = self.root / "changed-C.json"
        path.write_bytes(_canonical_file(changed))
        with self.assertRaisesRegex(ScoringTruthEventExecutionError, "identity"):
            validate_scoring_truth_event_adjudication_submission(path, self.bundle_c)

    def test_expected_manifest_names_are_not_generated_as_release_instances(self) -> None:
        self.assertTrue((self.bundle_a / EXECUTION_MANIFEST_NAME).is_file())
        self.assertTrue((self.bundle_c / ADJUDICATION_MANIFEST_NAME).is_file())
        self.assertFalse((ROOT / "data" / "annotations" / "m93").exists())


if __name__ == "__main__":
    unittest.main()
