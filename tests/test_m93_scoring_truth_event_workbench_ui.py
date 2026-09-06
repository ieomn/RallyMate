from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "rallymate_annotation" / "assets"
CORE = ASSETS / "scoring-truth-event-collection-core.js"
WORKBENCH = ASSETS / "scoring-truth-event-collection-workbench.js"
STYLESHEET = ASSETS / "scoring-truth-event-collection-workbench.css"
TEMPLATE = ASSETS / "scoring-truth-event-collection-workbench.html"


class M93ScoringTruthEventWorkbenchUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.core_source = CORE.read_text(encoding="utf-8")
        cls.workbench_source = WORKBENCH.read_text(encoding="utf-8")
        cls.stylesheet = STYLESHEET.read_text(encoding="utf-8")
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def _run_node(self, scenario: str) -> dict:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        source = textwrap.dedent(
            f"""
            const assert = require("assert");
            const Core = require({json.dumps(str(CORE))});
            const H = (letter) => letter.repeat(64);
            const bundleRef = (name, letter) => ({{
              bundle_id: name,
              manifest_binding_sha256: H(letter),
            }});
            const tasks = [1, 2, 3].map((number) => ({{
              task_id: `task-${{number}}`,
              video_id: `video-${{number}}`,
              media_path: `media/video-${{number}}.mp4`,
              media_sha256: H(String(number)),
              duration_ms: 12000 + number * 1000,
              frame_rate: {{numerator: number === 1 ? 30000 : 25, denominator: number === 1 ? 1001 : 1}},
              full_video_review_required: true,
            }}));
            const phaseKeys = {{
              FS01: ["preload_ms", "takeoff_proxy_ms", "landing_proxy_ms", "redistribution_ms", "initiation_ms"],
              FS02: ["direction_conversion_ms", "support_extension_proxy_ms", "lead_foot_motion_onset_proxy_ms", "first_step_slowdown_proxy_ms"],
              FS09: ["peak_speed_ms", "deceleration_peak_ms", "restabilization_onset_ms", "stable_control_onset_ms"],
            }};
            const safety = {{
              annotation_execution_authorized: true,
              operator_release_record_verified: true,
              mutation_enabled: true,
              export_enabled: true,
              full_video_only: true,
              machine_event_boundaries_embedded: false,
              phase_values_embedded: false,
              machine_keypoints_embedded: false,
              grades_or_thresholds_supported: false,
              calibration_authorized: false,
              promotion_authorized: false,
              production_enabled: false,
              maturity_promoted: false,
            }};
            const executionRefs = {{
              A: bundleRef("m93-execution-A", "A"),
              B: bundleRef("m93-execution-B", "C"),
            }};
            function executionBoot(role, overrides = {{}}) {{
              return {{
                schema_version: "1.0.0",
                workbench_version: Core.WORKBENCH_VERSION,
                bundle_version: "m93-v1",
                execution_id: "m93-execution-001",
                bundle_status: Core.EXECUTION_BUNDLE_STATUS,
                authorization_status: Core.AUTHORIZATION_STATUS,
                authorization_binding_sha256: H("E"),
                role_slot: role,
                ...safety,
                import_enabled: false,
                event_codes: ["FS01", "FS02", "FS09"],
                phase_keys_by_event: phaseKeys,
                required_annotator_slots: ["A", "B"],
                reviewer_slot: "C",
                tasks,
                execution_bundle: executionRefs[role],
                ...overrides,
              }};
            }}
            function observations(code, start, reverse = false) {{
              const keys = phaseKeys[code];
              return Object.fromEntries(keys.map((key, index) => [key, {{
                status: "observed",
                timestamp_ms: start + (reverse ? keys.length - index : index + 1) * 100,
                reason: "",
              }}]));
            }}
            function event(role, task, index, code, reverse = false) {{
              const start = 1000 + index * 2000;
              return {{
                annotation_id: `m93:${{role}}:${{task.video_id}}:event-${{String(index + 1).padStart(4, "0")}}`,
                event_code: code,
                start_ms: start,
                end_ms: start + 1000,
                phase_observations: observations(code, start, reverse),
                confidence_milli: 900,
                boundary_uncertainty_ms: 40,
                notes: "",
              }};
            }}
            function buildState(boot, participant, clockBase) {{
              const state = Core.createState(boot, participant);
              boot.tasks.forEach((task, index) => {{
                const code = ["FS01", "FS02", "FS09"][index];
                const decision = event(boot.role_slot, task, 0, code, code === "FS01");
                state.videos[task.task_id].events[decision.annotation_id] = Core.saveEventDraft(
                  null, decision, boot, task, `${{clockBase}}T00:00:0${{index + 1}}.000Z`,
                );
                state.videos[task.task_id].full_video_review = Core.saveReviewDraft(
                  null, {{completed: true, notes: `review-${{task.video_id}}`}}, `${{clockBase}}T00:00:1${{index + 1}}.000Z`,
                );
              }});
              return state;
            }}
            function expectThrow(fn, pattern) {{
              let error = null;
              try {{ fn(); }} catch (caught) {{ error = caught; }}
              assert(error, "expected an exception");
              if (pattern) assert.match(error.message, pattern);
            }}
            async function expectReject(promise, pattern) {{
              let error = null;
              try {{ await promise; }} catch (caught) {{ error = caught; }}
              assert(error, "expected a rejection");
              if (pattern) assert.match(error.message, pattern);
            }}

            (async () => {{
            {scenario}
            console.log(JSON.stringify({{ok: true}}));
            }})().catch((error) => {{ console.error(error && error.stack || error); process.exitCode = 1; }});
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "m93-ui-behavior.js"
            script.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [node, str(script)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        return json.loads(completed.stdout.strip())

    def test_all_assets_exist_and_javascript_has_valid_syntax(self) -> None:
        for path in (CORE, WORKBENCH, STYLESHEET, TEMPLATE):
            self.assertTrue(path.is_file(), path)
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        for path in (CORE, WORKBENCH):
            completed = subprocess.run(
                [node, "--check", str(path)], capture_output=True, text=True, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_template_is_one_role_fixed_bootstrap_template(self) -> None:
        marker = '<script id="scoring-truth-event-collection-bootstrap" type="application/json">{}</script>'
        self.assertEqual(self.template.count(marker), 1)
        self.assertLess(
            self.template.index("scoring-truth-event-collection-core.js"),
            self.template.index("scoring-truth-event-collection-workbench.js"),
        )
        self.assertNotIn('id="mode"', self.template)
        self.assertIn('id="annotator-workspace"', self.template)
        self.assertIn('id="reviewer-workspace"', self.template)
        self.assertIn('id="workbench-video"', self.template)
        self.assertIn('id="c-review-completed"', self.template)
        self.assertIn("confidence_milli 0–1000", self.template)

    def test_dom_controller_has_media_gate_storage_and_exact_import_contracts(self) -> None:
        for term in (
            "mediaReady",
            "video.seekable",
            "task.duration_ms",
            "localStorage.getItem",
            "localStorage.setItem",
            "Core.storageKey",
            "Core.parseCanonicalJsonBytes",
            "Core.sha256Bytes",
            "Core.validateAnnotationPair",
            "Core.markStaleAdjudications",
            "beforeunload",
        ):
            self.assertIn(term, self.workbench_source)
        self.assertIn('boot.role_slot === "C"', self.workbench_source)
        self.assertNotIn("annotation_confidence", self.core_source + self.workbench_source)
        self.assertNotIn("manifest_raw_sha256", self.core_source + self.workbench_source)
        self.assertNotIn("content_root_sha256", self.core_source + self.workbench_source)
        self.assertIn("currentVideoState().full_video_review = null", self.workbench_source)
        self.assertIn("state.video_adjudications[currentTaskId] = null", self.workbench_source)

    def test_bootstrap_exact_shape_status_and_storage_isolation(self) -> None:
        result = self._run_node(
            """
            const bootA = executionBoot("A");
            const bootB = executionBoot("B");
            Core.validateBootstrap(bootA);
            Core.validateBootstrap(bootB);
            expectThrow(() => Core.validateBootstrap({...bootA, extra: true}), /keys must exactly equal/);
            expectThrow(() => Core.validateBootstrap({...bootA, bundle_status: Core.ADJUDICATION_BUNDLE_STATUS}), /bundle_status/);
            expectThrow(() => Core.validateBootstrap({...bootA, import_enabled: true}), /import_enabled/);
            expectThrow(() => Core.validateBootstrap({...bootA, execution_bundle: {...bootA.execution_bundle, manifest_binding_sha256: H("a")}}), /uppercase SHA-256/);
            const keyA = await Core.storageKey(bootA, "A", "Annotator.One");
            const keyANormalized = await Core.storageKey(bootA, "A", "  annotator.one  ");
            const keyAOther = await Core.storageKey(bootA, "A", "Annotator.Two");
            const keyB = await Core.storageKey(bootB, "B", "Annotator.One");
            const reboundBootA = executionBoot("A", {execution_bundle: {...executionRefs.A, manifest_binding_sha256: H("F")}});
            const reboundKeyA = await Core.storageKey(reboundBootA, "A", "Annotator.One");
            assert.strictEqual(keyA, keyANormalized);
            assert.notStrictEqual(keyA, keyAOther);
            assert.notStrictEqual(keyA, keyB);
            assert.notStrictEqual(keyA, reboundKeyA);
            assert.match(keyA, /:A:[0-9A-F]{64}$/);
            """
        )
        self.assertEqual(result, {"ok": True})

    def test_annotation_revisions_are_stable_and_only_edited_video_changes(self) -> None:
        result = self._run_node(
            """
            const bootA = executionBoot("A");
            const stateA = buildState(bootA, "annotator-A", "2026-09-01");
            const first = await Core.buildAnnotationSubmission(bootA, stateA, {now: "2026-09-01T01:00:00.000Z"});
            await Core.validateAnnotationSubmission(bootA, first);
            const second = await Core.buildAnnotationSubmission(bootA, stateA, {now: "2026-09-01T02:00:00.000Z", previousSubmission: first});
            assert.strictEqual(first.submission_id, second.submission_id);
            assert.strictEqual(first.submitted_at, second.submitted_at);
            assert.strictEqual(first.submission_revision_sha256, second.submission_revision_sha256);
            assert.notStrictEqual(first.exported_at, second.exported_at);
            assert.deepStrictEqual(first.videos.map(v => v.video_revision_sha256), second.videos.map(v => v.video_revision_sha256));

            const lateZeroEventReview = Core.clone(first);
            const lateTask = tasks[2];
            const lateVideo = lateZeroEventReview.videos[2];
            lateVideo.events = [];
            lateVideo.full_video_review.reviewed_at = "2026-09-01T01:30:00.000Z";
            const lateReviewBase = {
              completed: lateVideo.full_video_review.completed,
              notes: lateVideo.full_video_review.notes,
              reviewed_at: lateVideo.full_video_review.reviewed_at,
            };
            lateVideo.full_video_review.review_revision_sha256 = await Core.sha256Canonical({
              execution_id: bootA.execution_id,
              execution_bundle: bootA.execution_bundle,
              authorization_binding_sha256: bootA.authorization_binding_sha256,
              role_slot: "A",
              annotator_id: lateZeroEventReview.annotator_id,
              task_id: lateTask.task_id,
              video_id: lateTask.video_id,
              full_video_review: lateReviewBase,
            });
            lateVideo.video_revision_sha256 = await Core.sha256Canonical({
              execution_id: bootA.execution_id,
              execution_bundle: bootA.execution_bundle,
              authorization_binding_sha256: bootA.authorization_binding_sha256,
              role_slot: "A",
              annotator_id: lateZeroEventReview.annotator_id,
              task_id: lateVideo.task_id,
              video_id: lateVideo.video_id,
              full_video_review: lateVideo.full_video_review,
              events: lateVideo.events,
            });
            const lateRevisionPayload = Object.fromEntries(Object.entries(lateZeroEventReview).filter(([key]) => !["submission_revision_sha256", "exported_at"].includes(key)));
            lateZeroEventReview.submission_revision_sha256 = await Core.sha256Canonical(lateRevisionPayload);
            await expectReject(Core.validateAnnotationSubmission(bootA, lateZeroEventReview), /review time exceeds submitted_at/);

            const task = tasks[1];
            const id = Object.keys(stateA.videos[task.task_id].events)[0];
            const previous = stateA.videos[task.task_id].events[id];
            const edited = {...Object.fromEntries([
              "annotation_id", "event_code", "start_ms", "end_ms", "phase_observations",
              "confidence_milli", "boundary_uncertainty_ms", "notes",
            ].map(key => [key, previous[key]])), notes: "operator edit"};
            stateA.videos[task.task_id].events[id] = Core.saveEventDraft(previous, edited, bootA, task, "2026-09-01T03:00:00.000Z");
            await expectReject(Core.buildAnnotationSubmission(bootA, stateA, {now: "2026-09-01T03:01:00.000Z", previousSubmission: second}), /newer than full-video review/);
            stateA.videos[task.task_id].full_video_review = null;
            stateA.videos[task.task_id].full_video_review = Core.saveReviewDraft(
              null, {completed: true, notes: `review-${task.video_id}`}, "2026-09-01T03:00:01.000Z",
            );
            const third = await Core.buildAnnotationSubmission(bootA, stateA, {now: "2026-09-01T03:01:00.000Z", previousSubmission: second});
            assert.strictEqual(third.submission_id, first.submission_id);
            assert.notStrictEqual(third.submission_revision_sha256, second.submission_revision_sha256);
            assert.strictEqual(third.videos[0].video_revision_sha256, second.videos[0].video_revision_sha256);
            assert.notStrictEqual(third.videos[1].video_revision_sha256, second.videos[1].video_revision_sha256);
            assert.strictEqual(third.videos[2].video_revision_sha256, second.videos[2].video_revision_sha256);
            assert.notStrictEqual(third.submitted_at, second.submitted_at);

            const invalidPrefix = event("A", tasks[0], 2, "FS02");
            invalidPrefix.annotation_id = "m93:B:video-1:event-0003";
            expectThrow(() => Core.validateEventDecision(invalidPrefix, bootA, tasks[0]), /must start with m93:A:/);
            const fractionalConfidence = event("A", tasks[0], 2, "FS02");
            fractionalConfidence.confidence_milli = 0.9;
            expectThrow(() => Core.validateEventDecision(fractionalConfidence, bootA, tasks[0]), /integer in 0\.\.1000/);
            const reversedFs09 = event("A", tasks[0], 2, "FS09", true);
            expectThrow(() => Core.validateEventDecision(reversedFs09, bootA, tasks[0]), /monotonic/);
            Core.validateEventDecision(event("A", tasks[0], 2, "FS01", true), bootA, tasks[0]);

            const sortState = Core.createState(bootA, "sort-A");
            tasks.forEach((sortTask, index) => {
              sortState.videos[sortTask.task_id].full_video_review = Core.saveReviewDraft(
                null, {completed: true, notes: "codepoint order review"}, `2026-09-01T04:00:0${index}.000Z`,
              );
            });
            for (const suffix of ["_x", "-x"]) {
              const sameStart = event("A", tasks[0], 0, "FS01");
              sameStart.annotation_id = `m93:A:video-1:${suffix}`;
              sortState.videos[tasks[0].task_id].events[sameStart.annotation_id] = Core.saveEventDraft(
                null, sameStart, bootA, tasks[0], "2026-09-01T03:00:00.000Z",
              );
            }
            const sortedA = await Core.buildAnnotationSubmission(bootA, sortState, {now: "2026-09-01T05:00:00.000Z"});
            assert.deepStrictEqual(
              sortedA.videos[0].events.map((row) => row.annotation_id),
              ["m93:A:video-1:-x", "m93:A:video-1:_x"],
            );
            """
        )
        self.assertEqual(result, {"ok": True})

    def test_c_import_is_raw_pinned_and_adjudication_stales_per_video_revision(self) -> None:
        result = self._run_node(
            """
            const bootA = executionBoot("A");
            const bootB = executionBoot("B");
            const stateA = buildState(bootA, "annotator-A", "2026-09-01");
            const stateB = buildState(bootB, "annotator-B", "2026-09-01");
            const submissionA = await Core.buildAnnotationSubmission(bootA, stateA, {now: "2026-09-01T01:00:00.000Z"});
            const submissionB = await Core.buildAnnotationSubmission(bootB, stateB, {now: "2026-09-01T01:00:01.000Z"});
            const bytesA = new TextEncoder().encode(Core.serializeCanonicalFile(submissionA));
            const bytesB = new TextEncoder().encode(Core.serializeCanonicalFile(submissionB));
            const rawA = await Core.sha256Bytes(bytesA);
            const rawB = await Core.sha256Bytes(bytesB);
            const bootC = {
              schema_version: "1.0.0", workbench_version: Core.WORKBENCH_VERSION,
              bundle_version: "m93-v1", execution_id: "m93-execution-001",
              bundle_status: Core.ADJUDICATION_BUNDLE_STATUS,
              authorization_status: Core.AUTHORIZATION_STATUS,
              authorization_binding_sha256: H("E"), role_slot: "C",
              ...safety, import_enabled: true,
              event_codes: ["FS01", "FS02", "FS09"], phase_keys_by_event: phaseKeys,
              required_annotator_slots: ["A", "B"], reviewer_slot: "C", tasks,
              adjudication_bundle: bundleRef("m93-adjudication-C", "B"),
              source_execution_bundles: executionRefs,
              expected_source_submissions: {
                A: {submission_id: submissionA.submission_id, raw_sha256: rawA, submission_revision_sha256: submissionA.submission_revision_sha256, annotator_id: submissionA.annotator_id},
                B: {submission_id: submissionB.submission_id, raw_sha256: rawB, submission_revision_sha256: submissionB.submission_revision_sha256, annotator_id: submissionB.annotator_id},
              },
            };
            Core.validateBootstrap(bootC);
            await expectReject(Core.validateAnnotationSubmission(bootC, submissionA, {rawSha256: H("F")}), /does not exactly match/);
            const recordA = {raw_sha256: rawA, submission: submissionA};
            const recordB = {raw_sha256: rawB, submission: submissionB};
            const pair = await Core.validateAnnotationPair(bootC, [recordA, recordB], "reviewer-C");
            await expectReject(Core.validateAnnotationPair(bootC, [recordA, recordB], "ANNOTATOR-a"), /reviewer identity/);

            const stateC = Core.createState(bootC, "reviewer-C");
            stateC.imports = {A: recordA, B: recordB};
            for (const slot of ["A", "B"]) {
              for (const video of pair[slot].videos) {
                for (const source of video.events) {
                  const id = `m93:C:${video.video_id}:adjudication-${slot}`;
                  const input = {
                    adjudication_id: id,
                    video_id: video.video_id,
                    decision_status: "rejected_sources",
                    source_annotation_revisions: [{role_slot: slot, annotation_id: source.annotation_id, annotation_revision_sha256: source.annotation_revision_sha256, relation: "rejected_source"}],
                    event: null,
                    decision_reason: "reviewed and rejected",
                  };
                  stateC.adjudications[id] = Core.saveAdjudicationDraft(null, input, bootC, pair, `2026-09-01T04:00:0${slot === "A" ? 1 : 2}.000Z`);
                }
              }
            }
            assert.strictEqual(Core.coverageReport(stateC.adjudications, pair).complete, true);
            await expectReject(Core.buildAdjudicationSubmission(bootC, stateC, {now: "2026-09-01T05:00:00.000Z"}), /video adjudication review is incomplete/);
            tasks.forEach((task, index) => {
              stateC.video_adjudications[task.task_id] = Core.saveVideoAdjudicationReview(
                null, {completed: true, notes: `C reviewed ${task.video_id}`}, bootC, task, pair,
                `2026-09-01T04:10:0${index}.000Z`,
              );
            });
            const firstC = await Core.buildAdjudicationSubmission(bootC, stateC, {now: "2026-09-01T05:00:00.000Z"});
            const secondC = await Core.buildAdjudicationSubmission(bootC, stateC, {now: "2026-09-01T06:00:00.000Z", previousSubmission: firstC});
            assert.strictEqual(firstC.adjudicated_at, secondC.adjudicated_at);
            assert.strictEqual(firstC.adjudication_submission_revision_sha256, secondC.adjudication_submission_revision_sha256);
            assert.notStrictEqual(firstC.exported_at, secondC.exported_at);
            assert.strictEqual(firstC.status, Core.ADJUDICATION_SUBMISSION_STATUS);
            assert.strictEqual(firstC.video_adjudications.length, 3);
            assert.deepStrictEqual(Object.keys(firstC.video_adjudications[0]).sort(), ["adjudicated_at", "completed", "notes", "review_revision_sha256", "task_id", "video_id"]);

            const changedPair = Core.clone(pair);
            changedPair.A.videos[1].video_revision_sha256 = H("F");
            const stale = Core.markStaleAdjudications(stateC.adjudications, changedPair);
            const clearedReviews = Core.markStaleVideoAdjudicationReviews(stateC.video_adjudications, bootC, changedPair);
            const staleIds = [...new Set(Object.values(stale).filter(row => row.stale).map(row => row.video_id))];
            assert.deepStrictEqual(staleIds, ["video-2"]);
            assert(clearedReviews["task-1"]);
            assert.strictEqual(clearedReviews["task-2"], null);
            assert(clearedReviews["task-3"]);
            stateC.adjudications = stale;
            stateC.video_adjudications = clearedReviews;
            assert.strictEqual(Core.coverageReport(stale, changedPair).complete, false);
            await expectReject(Core.buildAdjudicationSubmission(bootC, stateC, {now: "2026-09-01T07:00:00.000Z"}), /coverage is incomplete/);
            """
        )
        self.assertEqual(result, {"ok": True})

    def test_canonical_file_parser_rejects_noncanonical_or_invalid_utf8(self) -> None:
        result = self._run_node(
            """
            const value = {z: [1, true, null], a: "ok"};
            const canonical = Core.serializeCanonicalFile(value);
            assert.deepStrictEqual(Core.parseCanonicalJsonText(canonical), value);
            assert.strictEqual(Core.canonicalJson({"2": "later", "10": "first"}), '{"10":"first","2":"later"}');
            expectThrow(() => Core.parseCanonicalJsonText(JSON.stringify(value, null, 2) + "\\n"), /not canonical/);
            expectThrow(() => Core.parseCanonicalJsonText(Core.canonicalJson(value)), /followed by one newline/);
            expectThrow(() => Core.parseCanonicalJsonText(canonical + "\\n"), /one newline/);
            expectThrow(() => Core.parseCanonicalJsonText('{"a":1,"a":1}\\n'), /not canonical/);
            expectThrow(() => Core.parseCanonicalJsonBytes(new Uint8Array([0xC3, 0x28])), /valid UTF-8/);
            """
        )
        self.assertEqual(result, {"ok": True})

    def test_zero_source_events_still_require_three_c_video_confirmations(self) -> None:
        result = self._run_node(
            """
            const bootA = executionBoot("A");
            const bootB = executionBoot("B");
            const stateA = Core.createState(bootA, "zero-A");
            const stateB = Core.createState(bootB, "zero-B");
            for (const [state, boot] of [[stateA, bootA], [stateB, bootB]]) {
              tasks.forEach((task, index) => {
                state.videos[task.task_id].full_video_review = Core.saveReviewDraft(
                  null, {completed: true, notes: "confirmed zero visible target events"},
                  `2026-09-01T00:00:0${index + 1}.000Z`,
                );
              });
            }
            const submissionA = await Core.buildAnnotationSubmission(bootA, stateA, {now: "2026-09-01T01:00:00.000Z"});
            const submissionB = await Core.buildAnnotationSubmission(bootB, stateB, {now: "2026-09-01T01:00:01.000Z"});
            const rawA = await Core.sha256Bytes(new TextEncoder().encode(Core.serializeCanonicalFile(submissionA)));
            const rawB = await Core.sha256Bytes(new TextEncoder().encode(Core.serializeCanonicalFile(submissionB)));
            const bootC = {
              schema_version: "1.0.0", workbench_version: Core.WORKBENCH_VERSION,
              bundle_version: "m93-v1", execution_id: "m93-execution-001",
              bundle_status: Core.ADJUDICATION_BUNDLE_STATUS,
              authorization_status: Core.AUTHORIZATION_STATUS,
              authorization_binding_sha256: H("E"), role_slot: "C",
              ...safety, import_enabled: true,
              event_codes: ["FS01", "FS02", "FS09"], phase_keys_by_event: phaseKeys,
              required_annotator_slots: ["A", "B"], reviewer_slot: "C", tasks,
              adjudication_bundle: bundleRef("m93-zero-adjudication-C", "B"),
              source_execution_bundles: executionRefs,
              expected_source_submissions: {
                A: {submission_id: submissionA.submission_id, raw_sha256: rawA, submission_revision_sha256: submissionA.submission_revision_sha256, annotator_id: submissionA.annotator_id},
                B: {submission_id: submissionB.submission_id, raw_sha256: rawB, submission_revision_sha256: submissionB.submission_revision_sha256, annotator_id: submissionB.annotator_id},
              },
            };
            const records = {
              A: {raw_sha256: rawA, submission: submissionA},
              B: {raw_sha256: rawB, submission: submissionB},
            };
            const pair = await Core.validateAnnotationPair(bootC, [records.A, records.B], "zero-C");
            assert.strictEqual(Core.sourceEventIndex(pair).size, 0);
            assert.strictEqual(Core.coverageReport({}, pair).complete, true);
            const stateC = Core.createState(bootC, "zero-C");
            stateC.imports = records;
            await expectReject(Core.buildAdjudicationSubmission(bootC, stateC, {now: "2026-09-01T03:00:00.000Z"}), /video adjudication review is incomplete/);
            tasks.forEach((task, index) => {
              stateC.video_adjudications[task.task_id] = Core.saveVideoAdjudicationReview(
                null, {completed: true, notes: "C independently confirmed zero events"},
                bootC, task, pair, `2026-09-01T02:00:0${index + 1}.000Z`,
              );
            });
            const output = await Core.buildAdjudicationSubmission(bootC, stateC, {now: "2026-09-01T03:00:00.000Z"});
            assert.strictEqual(output.video_adjudications.length, 3);
            assert.strictEqual(output.decisions.length, 0);

            const sortStateC = Core.createState(bootC, "zero-C-sort");
            sortStateC.imports = records;
            tasks.forEach((task, index) => {
              sortStateC.video_adjudications[task.task_id] = Core.saveVideoAdjudicationReview(
                null, {completed: true, notes: "C codepoint sort review"},
                bootC, task, pair, `2026-09-01T04:00:0${index + 1}.000Z`,
              );
            });
            for (const suffix of ["_x", "-x"]) {
              const source = event("C", tasks[0], 0, "FS01");
              const finalEvent = {
                event_id: `m93:C:video-1:event-${suffix}`,
                event_code: source.event_code,
                start_ms: source.start_ms,
                end_ms: source.end_ms,
                phase_observations: source.phase_observations,
                confidence_milli: source.confidence_milli,
                boundary_uncertainty_ms: source.boundary_uncertainty_ms,
                notes: source.notes,
              };
              const input = {
                adjudication_id: `m93:C:video-1:${suffix}`,
                video_id: tasks[0].video_id,
                decision_status: "c_added_event",
                source_annotation_revisions: [],
                event: finalEvent,
                decision_reason: "reviewer-added sort fixture",
              };
              sortStateC.adjudications[input.adjudication_id] = Core.saveAdjudicationDraft(
                null, input, bootC, pair, "2026-09-01T03:30:00.000Z",
              );
            }
            const sortedC = await Core.buildAdjudicationSubmission(bootC, sortStateC, {now: "2026-09-01T05:00:00.000Z"});
            assert.deepStrictEqual(
              sortedC.decisions.map((row) => row.adjudication_id),
              ["m93:C:video-1:-x", "m93:C:video-1:_x"],
            );
            """
        )
        self.assertEqual(result, {"ok": True})


if __name__ == "__main__":
    unittest.main()
