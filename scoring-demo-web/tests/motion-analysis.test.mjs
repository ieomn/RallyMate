import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/motion-analysis.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { motionAnalysisOf, motionMetricText } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const episode = { episode_id: "a", family: "serve", start_ms: 100, peak_ms: 250, end_ms: 400, contact_confirmed: false, classification: { label_zh: "发球动作", status: "rule_inferred" }, metrics: {}, phases: [{ phase: "preparation", label_zh: "准备", start_ms: 100, end_ms: 250 }, { phase: "acceleration", label_zh: "加速", start_ms: -100, end_ms: 400 }, null], limitations_zh: [] };
const wrap = episodes => ({ action_recognition: { motion_analysis: { schema_version: "1.0.0", contact_confirmed: false, families: { serve: { episodes } } } } });

test("motion parser retains measured stages while rejecting mixed families, duplicate and invalid intervals", () => {
  const view = motionAnalysisOf(wrap([episode, episode, { ...episode, episode_id: "b", peak_ms: 800 }, { ...episode, episode_id: "c", family: "baseline" }, { ...episode, episode_id: "d", contact_confirmed: true }]));
  assert.equal(view.families.serve.episodes.length, 1);
  assert.equal(view.families.serve.episodes[0].phases.length, 1);
  assert.equal(view.contact_confirmed, false);
  assert.equal(view.status, "available");
});

test("motion parser supports assessment fallback but never invents a motion analysis from candidates", () => {
  assert.equal(motionAnalysisOf(null, wrap([episode])).families.serve.episodes.length, 1);
  assert.equal(motionAnalysisOf({ action_recognition: { candidates: [episode] } }), null);
  for (const invalid of [null, NaN, Infinity, "1", -1]) assert.equal(motionMetricText(invalid), "—");
  assert.equal(motionMetricText(0), "0.00");
});

test("partial phases preserve missing evidence instead of inventing zero-time stage boundaries", () => {
  const partial = { ...episode, analysis_status: "partial", phase_timing_status: "estimated_from_2d_motion", metric_notes_zh: ["肩线投影缩短，未输出肩线变化。", null], phases: [{ phase: "acceleration", label_zh: "加速", start_ms: 150, end_ms: 250, status: "measured" }, { phase: "follow_through", label_zh: "随挥", start_ms: null, end_ms: null, status: "unavailable" }] };
  const parsed = motionAnalysisOf(wrap([partial])).families.serve.episodes[0];
  assert.equal(parsed.analysis_status, "partial");
  assert.equal(parsed.phases.length, 2);
  assert.equal(parsed.phases[1].start_ms, null);
  assert.equal(parsed.phases[1].status, "unavailable");
  assert.deepEqual(parsed.metric_notes_zh, ["肩线投影缩短，未输出肩线变化。"]);
});
