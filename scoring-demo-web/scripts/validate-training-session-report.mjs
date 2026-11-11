// Verify a recomputed real report through UI parsing, SSR and portable export.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(file) {
  if (cache.has(file.href)) return cache.get(file.href);
  const source = fs.readFileSync(file, "utf8");
  let js = file.pathname.endsWith(".json") ? `export default ${source}` : ts.transpileModule(source, {
    fileName: file.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  js = js.replace(/import "[^"]+\.css";/g, "").replace(/from "([^"]+)"/g, (_, name) => {
    if (!name.startsWith(".")) return `from ${JSON.stringify(import.meta.resolve(name))}`;
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)))}`;
  });
  const output = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, output); return output;
}
const { validatedTennisAssessment } = await import(compiledUrl(new URL("../app/lib/tennis-assessment.ts", import.meta.url)));
const { trainingSessionAnalysisOf } = await import(compiledUrl(new URL("../app/lib/training-session-analysis.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/TrainingSessionPanel.tsx", import.meta.url)));
const { buildPracticeReport, renderReportMarkdown, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { readReportJson } = await import(compiledUrl(new URL("../app/lib/report-file.ts", import.meta.url)));
assert.ok(process.argv[2], "Pass a recomputed assessment JSON file");
const file = path.resolve(process.argv[2]), raw = JSON.parse(fs.readFileSync(file, "utf8"));
const context = { jobId: raw.source_run_id, videoSha256: raw.source_video_sha256, durationMs: raw.source_duration_ms };
const parsed = validatedTennisAssessment(raw, context);
assert.ok(parsed, "UI must accept source-bound assessment");
assert.ok(raw.training_session, "Backend must attach training session");
assert.deepEqual(parsed.training_session, raw.training_session, "UI must preserve all training session data");
for (const [index,episode] of raw.episodes.entries()) {
  if (episode.specialist_motion?.evidence?.subject_id) assert.deepEqual(parsed.episodes[index].specialist_motion, episode.specialist_motion, "UI must retain source-bound specialist evidence");
}
if (raw.rubric.specialist_motion) assert.deepEqual(parsed.rubric.specialist_motion,raw.rubric.specialist_motion,"UI must retain specialist rubric");
assert.deepEqual(trainingSessionAnalysisOf(raw.training_session), raw.training_session);
assert.ok(parsed.training_session.training.active_clips.length > 0);
const panel = renderToStaticMarkup(createElement(Panel, { analysis: parsed.training_session, sourceRunId: raw.source_run_id,
  sourceVideoSha256: raw.source_video_sha256, subjectId: raw.subject_scope.selected_subject_id, onSeek: () => {} }));
assert.match(panel, /动作片段/); assert.match(panel, /回看/); assert.match(panel, /关节角度/);
assert.doesNotMatch(panel, /比赛记录/);
const input = { mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "succeeded" },
  evidence: { jobId: raw.source_run_id, result: { job_id: raw.source_run_id, input: { video: { sha256: raw.source_video_sha256, duration_ms: raw.source_duration_ms } }, tennis_assessment: parsed } } };
const readable = buildPracticeReport(input), markdown = renderReportMarkdown(readable);
assert.match(markdown, /有效训练动作片段/); assert.match(markdown, /本次动作的关节活动范围/);
const json = JSON.stringify(buildReportBackup(input));
const restored = await readReportJson({ size: Buffer.byteLength(json), text: async () => json });
const reread = validatedTennisAssessment(restored.result.tennis_assessment, context);
if (!reread?.training_session) {
  const differences = [];
  function compare(a,b,trail="") {
    if (differences.length >= 12 || a === b) return;
    if (a && b && typeof a === "object" && typeof b === "object") {
      for (const key of new Set([...Object.keys(a),...Object.keys(b)])) compare(a[key],b[key],`${trail}.${key}`);
    } else differences.push({path:trail,before:a,after:b});
  }
  compare(parsed,restored.result.tennis_assessment);
  console.log(JSON.stringify({roundtrip_differences:differences,session_standalone_valid:Boolean(trainingSessionAnalysisOf(restored.result.tennis_assessment?.training_session))},null,2));
}
assert.ok(reread?.training_session, "Full JSON roundtrip must remain a valid assessment with training session");
assert.deepEqual(reread?.training_session, raw.training_session, "Full JSON roundtrip must preserve clip/ROM/match data");
for (const [index,episode] of parsed.episodes.entries()) if (episode.specialist_motion) assert.deepEqual(reread.episodes[index].specialist_motion,episode.specialist_motion,"Specialist evidence must survive full JSON export");
if (parsed.rubric.specialist_motion) assert.deepEqual(reread.rubric.specialist_motion,parsed.rubric.specialist_motion);
const output = { passed: true, evaluated_at: new Date().toISOString(), source_run_id: raw.source_run_id,
  source_video_sha256: raw.source_video_sha256, score: parsed.overall_score, episodes: parsed.episodes.length,
  training_clips: parsed.training_session.training.active_clips.length,
  active_duration_ms: parsed.training_session.training.active_duration_ms,
  joint_range_metrics: Object.keys(parsed.training_session.movement_ranges.metrics).length,
  frontend_training_preserved: true, panel_rendered: true, seek_buttons: (panel.match(/>回看</g) ?? []).length,
  readable_export_preserved: true, full_json_roundtrip_preserved: true, backup_bytes: Buffer.byteLength(json) };
if (process.argv[3]) fs.writeFileSync(path.resolve(process.argv[3]), JSON.stringify(output, null, 2));
console.log(JSON.stringify(output, null, 2));
