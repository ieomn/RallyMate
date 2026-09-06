from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "src" / "rallymate_annotation" / "assets" / "fs09-phase-truth-workbench.js"
CSS = ROOT / "src" / "rallymate_annotation" / "assets" / "fs09-phase-truth-workbench.css"
MODULE = ROOT / "src" / "rallymate_evaluation" / "fs09_phase_truth.py"

class FS09PhaseWorkbenchUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.javascript = JS.read_text(encoding="utf-8")
        cls.stylesheet = CSS.read_text(encoding="utf-8")
        cls.module_source = MODULE.read_text(encoding="utf-8")

    def _run_node_behavior(self, scenario: str) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        harness = textwrap.dedent(
            f"""
            const assert = require("assert");
            const {{webcrypto}} = require("crypto");
            const fs = require("fs");
            const vm = require("vm");
            const sourcePath = {json.dumps(str(JS))};
            const original = fs.readFileSync(sourcePath, "utf8");
            const marker = '  $("identity").value = ""; loadContext(contextFromControls()); requireHandoff();\\n}})();';
            assert(original.includes(marker), "workbench test-hook insertion marker drifted");
            const instrumented = original.replace(marker, `  $("identity").value = ""; loadContext(contextFromControls()); requireHandoff();
              window.__fs09Test = {{
                storageKey, handoffReady, handoffGateMessage, saveContext, annotationRows,
                adjudicationRows, validateImportedRows, transition, loadContext, contextKey,
                setDirty: (value) => {{ dirty = value; }},
                setImportedRows: async (rows) => {{ const combined = await validateImportedRows(rows); imported.clear(); combined.forEach((row, id) => imported.set(id, row)); return combined; }},
                getDrafts: () => JSON.parse(JSON.stringify(drafts)),
              }};
            }})();`);

            const NativeDate = Date;
            let clockMs = NativeDate.parse("2026-08-30T00:00:00.000Z");
            class ControlledDate extends NativeDate {{
              constructor(...args) {{
                if (args.length) super(...args);
                else {{ super(clockMs); clockMs += 1000; }}
              }}
              static parse(value) {{ return NativeDate.parse(value); }}
            }}
            global.Date = ControlledDate;

            const storageData = new Map();
            const localStorage = {{
              getItem: (key) => storageData.has(key) ? storageData.get(key) : null,
              setItem: (key, value) => storageData.set(key, String(value)),
              removeItem: (key) => storageData.delete(key),
            }};

            const decisionFields = [
              "event_present", "event_start_ms", "event_end_ms", "event_reason",
              "peak_speed_status", "peak_speed_ms", "peak_speed_reason",
              "deceleration_peak_status", "deceleration_peak_ms", "deceleration_peak_reason",
              "restabilization_onset_status", "restabilization_onset_ms", "restabilization_onset_reason",
              "stable_control_onset_status", "stable_control_onset_ms", "stable_control_onset_reason",
              "confidence", "notes",
            ];
            const tasks = [1, 2].map((number) => ({{
              task_id: `task-${{number}}`, source_time_offset_ms: number * 10000,
              review_start_ms: number * 10000, review_end_ms: number * 10000 + 4000,
              target_selection_anchor_ms: number * 10000 + 2000,
            }}));
            const validBundleOne = `m88-fs09-blind-${{"B".repeat(64)}}`;
            const validBundleTwo = `m88-fs09-blind-${{"C".repeat(64)}}`;
            function makeBoot(bundle = validBundleOne, plan = "A".repeat(64), authorized = true) {{
              return {{
                handoff_bundle_id: bundle, analysis_plan_sha256: plan, tasks,
                annotation_execution_authorized: authorized,
                external_protocol_receipt_verified: authorized,
                review_clips: Object.fromEntries(tasks.map((task) => [task.task_id, `media/${{task.task_id}}.mp4`])),
                review_clip_fps: Object.fromEntries(tasks.map((task) => [task.task_id, 24])),
                required_annotators: 2,
                annotation_fields: ["handoff_bundle_id", "analysis_plan_sha256", "annotation_id", "task_id", "annotator_id", ...decisionFields, "annotation_revision_sha256", "annotated_at", "exported_at"],
                adjudication_fields: ["handoff_bundle_id", "analysis_plan_sha256", "adjudication_id", "task_id", "source_annotation_ids", "source_annotation_revision_sha256s", "reviewer_id", ...decisionFields, "adjudicated_at", "exported_at", "status"],
              }};
            }}
            function validRecord(task, notes = "") {{
              const start = task.review_start_ms + 100;
              return {{
                event_present: "true", event_start_ms: String(start), event_end_ms: String(start + 900), event_reason: "",
                peak_speed_status: "observed", peak_speed_ms: String(start + 100), peak_speed_reason: "",
                deceleration_peak_status: "observed", deceleration_peak_ms: String(start + 200), deceleration_peak_reason: "",
                restabilization_onset_status: "observed", restabilization_onset_ms: String(start + 300), restabilization_onset_reason: "",
                stable_control_onset_status: "observed", stable_control_onset_ms: String(start + 400), stable_control_onset_reason: "",
                confidence: "0.9", notes,
              }};
            }}

            function launch(boot) {{
              const elements = new Map();
              const alerts = [];
              class Element {{
                constructor(tag = "div", id = "") {{
                  this.tagName = tag.toUpperCase(); this.id = id; this.value = "";
                  this.textContent = ""; this.className = ""; this.disabled = false;
                  this.hidden = false; this.dataset = {{}}; this.children = [];
                  this.listeners = new Map(); this.selectedIndex = 0; this.currentTime = 0;
                  this.duration = 4; this.error = null; this.src = ""; this.files = [];
                  this.seekable = {{length: 1, start: () => 0, end: () => 4}};
                }}
                addEventListener(name, listener) {{
                  if (!this.listeners.has(name)) this.listeners.set(name, []);
                  this.listeners.get(name).push(listener);
                }}
                async emit(name, extra = {{}}) {{
                  const event = {{target: this, preventDefault() {{}}, ...extra}};
                  for (const listener of this.listeners.get(name) || []) await listener(event);
                }}
                click() {{ return this.emit("click"); }}
                appendChild(child) {{
                  this.children.push(child);
                  if (this.tagName === "SELECT" && this.children.length === 1 && !this.value) this.value = child.value;
                  return child;
                }}
                insertAdjacentElement() {{}}
                replaceChildren(...children) {{ this.children = children; }}
                load() {{}}
                set innerHTML(value) {{ this._innerHTML = value; }}
                get innerHTML() {{ return this._innerHTML || ""; }}
              }}
              const ids = [
                "fs09-phase-truth-bootstrap", "video", "phases", "task", "mode", "identity", "editor",
                "event-present", "event-start", "event-end", "event-reason", "confidence", "notes", "status",
                "media-status", "window", "progress", "seek-start", "seek-anchor", "save", "previous", "next",
                "export-annotations", "export-adjudications", "import", "reset", "video-time", "video-fps",
                "comparison-status", "comparison-grid",
                "peak_speed-status", "peak_speed-ms", "peak_speed-reason",
                "deceleration_peak-status", "deceleration_peak-ms", "deceleration_peak-reason",
                "restabilization_onset-status", "restabilization_onset-ms", "restabilization_onset-reason",
                "stable_control_onset-status", "stable_control_onset-ms", "stable_control_onset-reason",
              ];
              for (const id of ids) elements.set(id, new Element(id === "task" || id === "mode" || id.endsWith("-status") || id === "event-present" ? "select" : id === "video" ? "video" : "div", id));
              elements.get("mode").value = "annotate";
              elements.get("fs09-phase-truth-bootstrap").textContent = JSON.stringify(boot);
              const document = {{
                getElementById: (id) => elements.get(id) || null,
                createElement: (tag) => new Element(tag),
                querySelectorAll: (selector) => {{
                  if (selector === "#export-annotations, #export-adjudications") return [elements.get("export-annotations"), elements.get("export-adjudications")];
                  if (selector.includes("#seek-start")) return [elements.get("seek-start"), elements.get("seek-anchor")];
                  return [];
                }},
              }};
              const windowObject = {{addEventListener() {{}}, __fs09Test: null}};
              global.document = document; global.window = windowObject; global.localStorage = localStorage; global.crypto = webcrypto;
              global.alert = (message) => alerts.push(String(message)); global.confirm = () => true;
              global.URL = {{createObjectURL: () => "blob:test", revokeObjectURL() {{}}}};
              vm.runInThisContext(instrumented, {{filename: sourcePath}});
              return {{hook: windowObject.__fs09Test, elements, alerts}};
            }}
            function setForm(environment, record) {{
              const element = environment.elements;
              element.get("event-present").value = record.event_present;
              element.get("event-start").value = record.event_start_ms;
              element.get("event-end").value = record.event_end_ms;
              element.get("event-reason").value = record.event_reason;
              element.get("confidence").value = record.confidence;
              element.get("notes").value = record.notes;
              for (const prefix of ["peak_speed", "deceleration_peak", "restabilization_onset", "stable_control_onset"]) {{
                element.get(`${{prefix}}-status`).value = record[`${{prefix}}_status`];
                element.get(`${{prefix}}-ms`).value = record[`${{prefix}}_ms`];
                element.get(`${{prefix}}-reason`).value = record[`${{prefix}}_reason`];
              }}
            }}

            async function annotationFixture(boot, task, annotator, suffix = "") {{
              const row = {{handoff_bundle_id: boot.handoff_bundle_id, analysis_plan_sha256: boot.analysis_plan_sha256, annotation_id: `${{annotator}}:${{task.task_id}}`, task_id: task.task_id, annotator_id: annotator, ...validRecord(task, suffix), annotated_at: "2026-08-30T00:00:01.000Z", exported_at: "2026-08-30T00:00:02.000Z"}};
              const keys = boot.annotation_fields.filter((name) => !["annotation_revision_sha256", "exported_at"].includes(name)).sort();
              const payload = JSON.stringify(Object.fromEntries(keys.map((name) => [name, row[name] == null ? "" : String(row[name])])));
              row.annotation_revision_sha256 = Buffer.from(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(payload))).toString("hex").toUpperCase();
              return row;
            }}

            (async () => {{
            {scenario}
            console.log(JSON.stringify({{ok: true}}));
            }})().catch((error) => {{ console.error(error && error.stack || error); process.exitCode = 1; }});
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "workbench-behavior.js"
            script.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [node, str(script)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(json.loads(completed.stdout.strip()), {"ok": True})

    def test_javascript_has_valid_node_syntax(self) -> None:
        node = shutil.which("node")
        if node is None: self.skipTest("Node.js is unavailable")
        completed = subprocess.run([node, "--check", str(JS)], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_import_is_strict_and_reviewer_independence_is_per_task(self) -> None:
        self.assertIn("requireAnnotationHeaders(parsed.headers)", self.javascript)
        self.assertIn("cells.length !== headers.length", self.javascript)
        self.assertIn("同一 annotator 每个任务只能提交一次", self.javascript)
        self.assertGreaterEqual(
            self.javascript.count("annotators.size !== boot.required_annotators"),
            2,
        )
        self.assertIn("annotators.size > boot.required_annotators", self.javascript)
        self.assertIn("annotators.has(reviewer)", self.javascript)

    def test_bootstrap_handoff_gate_blocks_invalid_sources(self) -> None:
        self._run_node_behavior(
            """
            const missing = launch(makeBoot("", "a".repeat(64)));
            assert.strictEqual(missing.hook.handoffReady, false);
            assert.strictEqual(missing.elements.get("save").disabled, true);
            assert.strictEqual(missing.elements.get("import").disabled, true);
            assert.strictEqual(missing.elements.get("export-annotations").disabled, true);
            assert.strictEqual(missing.hook.saveContext({mode: "annotate", identity: "A", taskId: "task-1"}, validRecord(tasks[0]), false), false);
            assert.match(missing.elements.get("status").textContent, /public blind handoff/);
            missing.elements.get("export-annotations").emit("click");
            assert.match(missing.alerts.at(-1), /public blind handoff/);

            const valid = launch(makeBoot());
            assert.strictEqual(valid.hook.handoffReady, true);
            assert.strictEqual(valid.elements.get("save").disabled, false);

            const technicalOnly = launch(makeBoot(validBundleOne, "A".repeat(64), false));
            assert.strictEqual(technicalOnly.hook.handoffReady, false);
            assert.strictEqual(technicalOnly.elements.get("save").disabled, true);
            assert.match(technicalOnly.elements.get("status").textContent, /外部协议回执/);

            const malformedBundle = launch(makeBoot("m88-public-blind-handoff-v1", "A".repeat(64)));
            assert.strictEqual(malformedBundle.hook.handoffReady, false);
            const lowercaseBundle = launch(makeBoot(`m88-fs09-blind-${"b".repeat(64)}`, "A".repeat(64)));
            assert.strictEqual(lowercaseBundle.hook.handoffReady, false);
            const paddedPlan = launch(makeBoot(validBundleOne, `${"A".repeat(64)} `));
            assert.strictEqual(paddedPlan.hook.handoffReady, false);
            const lowercasePlan = launch(makeBoot(validBundleOne, "a".repeat(64)));
            assert.strictEqual(lowercasePlan.hook.handoffReady, false);
            """
        )

    def test_decision_time_is_stable_across_exports_and_updates_only_after_reedit(self) -> None:
        self._run_node_behavior(
            """
            const environment = launch(makeBoot());
            const hook = environment.hook;
            const identity = "annotator-A";
            environment.elements.get("identity").value = identity;
            const first = {mode: "annotate", identity, taskId: "task-1"};
            const second = {mode: "annotate", identity, taskId: "task-2"};
            const firstRecord = validRecord(tasks[0], "first decision");
            assert.strictEqual(hook.saveContext(first, firstRecord, false), true);
            const initialFirstAt = hook.getDrafts()[hook.contextKey(first)].annotated_at;
            assert.strictEqual(hook.saveContext(first, {...firstRecord}, false), true);
            assert.strictEqual(hook.getDrafts()[hook.contextKey(first)].annotated_at, initialFirstAt);
            assert.strictEqual(hook.saveContext(second, validRecord(tasks[1]), false), true);
            const initialSecondAt = hook.getDrafts()[hook.contextKey(second)].annotated_at;

            const exportOne = await hook.annotationRows();
            const exportTwo = await hook.annotationRows();
            assert.strictEqual(exportOne.length, 2);
            assert.strictEqual(exportOne[0].annotated_at, initialFirstAt);
            assert.strictEqual(exportTwo[0].annotated_at, initialFirstAt);
            assert.strictEqual(exportOne[1].annotated_at, initialSecondAt);
            assert.strictEqual(exportTwo[1].annotated_at, initialSecondAt);
            assert.notStrictEqual(exportOne[0].exported_at, exportTwo[0].exported_at);
            assert.strictEqual(exportOne[0].exported_at, exportOne[1].exported_at);
            for (const row of exportTwo) {
              assert.strictEqual(row.handoff_bundle_id, validBundleOne);
              assert.strictEqual(row.analysis_plan_sha256, "A".repeat(64));
              assert.match(row.annotation_revision_sha256, /^[A-F0-9]{64}$/);
            }
            assert.strictEqual(Object.values(hook.getDrafts()).some((row) => Object.hasOwn(row, "exported_at")), false);

            const edited = {...firstRecord, notes: "materially edited decision"};
            assert.strictEqual(hook.saveContext(first, edited, false), true);
            const editedFirstAt = hook.getDrafts()[hook.contextKey(first)].annotated_at;
            assert.notStrictEqual(editedFirstAt, initialFirstAt);
            assert.strictEqual(hook.getDrafts()[hook.contextKey(second)].annotated_at, initialSecondAt);
            assert.strictEqual(hook.saveContext(first, {...edited}, false), true);
            assert.strictEqual(hook.getDrafts()[hook.contextKey(first)].annotated_at, editedFirstAt);
            """
        )

    def test_adjudication_time_is_stable_and_reedit_updates_it(self) -> None:
        self._run_node_behavior(
            """
            const boot = makeBoot();
            const environment = launch(boot);
            const hook = environment.hook;
            const sources = [];
            for (const task of tasks) for (const annotator of ["annotator-A", "annotator-B"]) sources.push(await annotationFixture(boot, task, annotator));
            await hook.setImportedRows(sources);
            const context = {mode: "adjudicate", identity: "reviewer-C", taskId: "task-1"};
            environment.elements.get("mode").value = context.mode;
            environment.elements.get("identity").value = context.identity;
            const initialRecord = validRecord(tasks[0], "initial adjudication");
            assert.strictEqual(hook.saveContext(context, initialRecord, false), true);
            const initialAt = hook.getDrafts()[hook.contextKey(context)].adjudicated_at;
            assert.ok(initialAt);
            assert.strictEqual(hook.getDrafts()[hook.contextKey(context)].annotated_at, undefined);

            assert.strictEqual(hook.saveContext(context, {...initialRecord}, false), true);
            assert.strictEqual(hook.getDrafts()[hook.contextKey(context)].adjudicated_at, initialAt);
            const exportOne = hook.adjudicationRows();
            const exportTwo = hook.adjudicationRows();
            assert.strictEqual(exportOne[0].adjudicated_at, initialAt);
            assert.ok(exportOne[0].source_annotation_revision_sha256s.includes(";"));
            assert.strictEqual(exportTwo[0].adjudicated_at, initialAt);
            assert.notStrictEqual(exportOne[0].exported_at, exportTwo[0].exported_at);
            assert.strictEqual(Object.hasOwn(hook.getDrafts()[hook.contextKey(context)], "exported_at"), false);

            const edited = {...initialRecord, notes: "edited adjudication"};
            assert.strictEqual(hook.saveContext(context, edited, false), true);
            const editedAt = hook.getDrafts()[hook.contextKey(context)].adjudicated_at;
            assert.notStrictEqual(editedAt, initialAt);
            assert.strictEqual(hook.saveContext(context, {...edited}, false), true);
            assert.strictEqual(hook.getDrafts()[hook.contextKey(context)].adjudicated_at, editedAt);
            """
        )

    def test_dirty_task_identity_and_mode_transitions_use_the_same_timestamp_rule(self) -> None:
        self._run_node_behavior(
            """
            const environment = launch(makeBoot());
            const hook = environment.hook;
            const identity = "annotator-A";
            const taskOne = {mode: "annotate", identity, taskId: "task-1"};
            const taskTwo = {mode: "annotate", identity, taskId: "task-2"};
            environment.elements.get("identity").value = identity;
            hook.loadContext(taskOne);
            setForm(environment, validRecord(tasks[0], "initial"));
            assert.strictEqual(hook.saveContext(taskOne, validRecord(tasks[0], "initial"), false), true);
            const beforeTaskSwitch = hook.getDrafts()[hook.contextKey(taskOne)].annotated_at;
            setForm(environment, validRecord(tasks[0], "edited before task switch"));
            hook.setDirty(true);
            assert.strictEqual(hook.transition(taskTwo), true);
            assert.notStrictEqual(hook.getDrafts()[hook.contextKey(taskOne)].annotated_at, beforeTaskSwitch);

            setForm(environment, validRecord(tasks[1], "initial second"));
            assert.strictEqual(hook.saveContext(taskTwo, validRecord(tasks[1], "initial second"), false), true);
            const beforeIdentitySwitch = hook.getDrafts()[hook.contextKey(taskTwo)].annotated_at;
            setForm(environment, validRecord(tasks[1], "edited before identity switch"));
            hook.setDirty(true);
            assert.strictEqual(hook.transition({mode: "annotate", identity: "annotator-B", taskId: "task-2"}), true);
            assert.notStrictEqual(hook.getDrafts()[hook.contextKey(taskTwo)].annotated_at, beforeIdentitySwitch);

            hook.loadContext(taskOne);
            const beforeModeSwitch = hook.getDrafts()[hook.contextKey(taskOne)].annotated_at;
            setForm(environment, validRecord(tasks[0], "edited before mode switch"));
            hook.setDirty(true);
            assert.strictEqual(hook.transition({mode: "adjudicate", identity, taskId: "task-1"}), true);
            assert.notStrictEqual(hook.getDrafts()[hook.contextKey(taskOne)].annotated_at, beforeModeSwitch);
            """
        )

    def test_local_storage_is_isolated_by_handoff_and_plan(self) -> None:
        self._run_node_behavior(
            """
            const first = launch(makeBoot(validBundleOne, "A".repeat(64)));
            const firstContext = {mode: "annotate", identity: "A", taskId: "task-1"};
            assert.strictEqual(first.hook.saveContext(firstContext, validRecord(tasks[0], "one"), false), true);
            assert.ok(first.hook.storageKey.includes(validBundleOne));
            assert.match(first.hook.storageKey, new RegExp("A{64}"));

            const second = launch(makeBoot(validBundleTwo, "A".repeat(64)));
            assert.notStrictEqual(second.hook.storageKey, first.hook.storageKey);
            assert.deepStrictEqual(second.hook.getDrafts(), {});
            assert.strictEqual(second.hook.saveContext(firstContext, validRecord(tasks[0], "two"), false), true);

            const third = launch(makeBoot(validBundleOne, "B".repeat(64)));
            assert.notStrictEqual(third.hook.storageKey, first.hook.storageKey);
            assert.deepStrictEqual(third.hook.getDrafts(), {});

            const restored = launch(makeBoot(validBundleOne, "A".repeat(64)));
            assert.strictEqual(restored.hook.getDrafts()[restored.hook.contextKey(firstContext)].notes, "one");
            assert.strictEqual(storageData.size, 2);
            """
        )

    def test_annotation_import_requires_exact_handoff_and_plan(self) -> None:
        self._run_node_behavior(
            """
            const environment = launch(makeBoot());
            const hook = environment.hook;
            const context = {mode: "annotate", identity: "annotator-A", taskId: "task-1"};
            environment.elements.get("identity").value = context.identity;
            assert.strictEqual(hook.saveContext(context, validRecord(tasks[0]), false), true);
            const row = (await hook.annotationRows())[0];
            assert.strictEqual((await hook.validateImportedRows([row])).size, 1);
            const reexported = {...row, exported_at: "2026-08-30T01:00:00.000Z"};
            assert.strictEqual((await hook.validateImportedRows([reexported])).size, 1);
            await assert.rejects(hook.validateImportedRows([{...row, handoff_bundle_id: "different-handoff"}]), /public blind handoff bundle.analysis plan 不匹配/);
            await assert.rejects(hook.validateImportedRows([{...row, analysis_plan_sha256: "B".repeat(64)}]), /public blind handoff bundle.analysis plan 不匹配/);
            await assert.rejects(hook.validateImportedRows([row, {...reexported, annotator_id: "annotator-B"}]), /annotation_id revision 冲突|annotation revision digest/);
            """
        )

    def test_reloaded_changed_annotation_revisions_require_every_task_to_be_readjudicated(self) -> None:
        self._run_node_behavior(
            """
            const boot = makeBoot();
            const first = launch(boot); const v1 = [];
            for (const task of tasks) for (const annotator of ["annotator-A", "annotator-B"]) v1.push(await annotationFixture(boot, task, annotator, "v1"));
            await first.hook.setImportedRows(v1);
            for (const task of tasks) {
              const context = {mode: "adjudicate", identity: "reviewer-C", taskId: task.task_id};
              assert.strictEqual(first.hook.saveContext(context, validRecord(task, "reviewed-v1"), false), true);
            }

            const second = launch(boot); const v2 = [];
            for (const task of tasks) for (const annotator of ["annotator-A", "annotator-B"]) v2.push(await annotationFixture(boot, task, annotator, "v2 changed"));
            await second.hook.setImportedRows(v2);
            const current = {mode: "adjudicate", identity: "reviewer-C", taskId: "task-1"};
            second.elements.get("mode").value = "adjudicate"; second.elements.get("identity").value = "reviewer-C";
            second.hook.loadContext(current);
            assert.strictEqual(second.hook.saveContext(current, validRecord(tasks[0], "reviewed-v1"), false), true);
            await second.elements.get("video").emit("loadedmetadata");
            await second.elements.get("export-adjudications").emit("click");
            assert.match(second.alerts.at(-1), /精确 annotation revisions|逐任务重新核对/);
            """
        )

    def test_media_gate_requires_full_seekable_range_and_disables_truth_actions(self) -> None:
        for marker in (
            "video.seekable.length",
            "video.seekable.start(index) <= tolerance",
            "video.seekable.end(index) >= duration - tolerance",
            "#export-annotations, #export-adjudications",
            "button.disabled = !mediaReady",
            "媒体门禁未通过",
        ):
            self.assertIn(marker, self.javascript)
        self.assertIn('id="media-status"', self.module_source)

    def test_event_phase_and_confidence_require_active_confirmation(self) -> None:
        self.assertIn('event_present: ""', self.javascript)
        self.assertNotIn('event_present: "true"', self.javascript)
        self.assertIn('record[`${prefix}_status`] = ""', self.javascript)
        self.assertIn('confidence: ""', self.javascript)
        self.assertIn("必须主动选择事件存在或不存在/不可观测", self.javascript)
        self.assertIn("必须主动选择阶段状态", self.javascript)
        self.assertIn("必须主动填写 confidence", self.javascript)
        self.assertIn('<select id="event-present">', self.module_source)

    def test_ids_reject_semicolon_and_newlines(self) -> None:
        self.assertIn("invalidIdCharacters", self.javascript)
        self.assertIn("不得包含分号或换行", self.javascript)
        self.assertIn("invalidIdCharacters.test(row.annotation_id)", self.javascript)
        self.assertIn('normalize("NFKC").toLowerCase()', self.javascript)
        self.assertIn("identityKey(row.annotator_id)", self.javascript)

    def test_authority_pack_requires_public_handoff_allowlist_server(self) -> None:
        self.assertIn("不得把本目录作为 HTTP root", self.module_source)
        self.assertIn(
            "请只分发由 M88 builder 生成并验证的 public blind handoff",
            self.module_source,
        )
        self.assertIn("technical-only handoff **不能**", self.module_source)
        self.assertIn("没有可执行的 A/B/C 或 intake 正向流程", self.module_source)
        self.assertIn("public blind handoff", self.javascript)
        self.assertIn("allowlist Range server", self.javascript)
        self.assertNotIn("scripts/range_http_server.py", self.javascript)

    def test_export_requires_successful_save(self) -> None:
        self.assertGreaterEqual(self.javascript.count("if (!save()) return;"), 2)

    def test_dirty_form_is_saved_or_transition_is_blocked(self) -> None:
        self.assertIn("const saveTarget = firstIdentity ? nextContext : activeContext", self.javascript)
        self.assertIn("activeContext && dirty && !saveContext(saveTarget", self.javascript)
        self.assertIn("已阻止切换以避免静默丢稿", self.javascript)
        self.assertIn('window.addEventListener("beforeunload"', self.javascript)

    def test_reviewer_comparison_has_all_manual_fields_and_diff_marker(self) -> None:
        for field in ("event_present", "event_start_ms", "event_end_ms", "event_reason", "peak_speed_status", "peak_speed_ms", "peak_speed_reason", "deceleration_peak_status", "deceleration_peak_ms", "deceleration_peak_reason", "restabilization_onset_status", "restabilization_onset_ms", "restabilization_onset_reason", "stable_control_onset_status", "stable_control_onset_ms", "stable_control_onset_reason", "confidence", "notes"):
            self.assertIn(field, self.javascript)
        self.assertIn("new Set(values).size > 1", self.javascript)
        self.assertIn("comparison-row", self.stylesheet)

    def test_frame_and_millisecond_controls_use_bootstrap_fps(self) -> None:
        self.assertIn("boot.review_clip_fps", self.javascript)
        for marker in ('data-step-frame="-1"', 'data-step-frame="1"', 'data-step-ms="-100"', 'data-step-ms="100"', "source_time_offset_ms + local"):
            self.assertIn(marker, self.javascript)

    def test_sealed_candidate_and_pose_data_are_not_loaded(self) -> None:
        lowered = self.javascript.lower()
        self.assertNotIn("sealed-event-candidates", lowered)
        self.assertNotIn("candidate_phases", lowered)
        self.assertNotIn("pose_records", lowered)

if __name__ == "__main__": unittest.main()
