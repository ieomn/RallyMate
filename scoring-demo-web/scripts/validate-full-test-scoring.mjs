// Exercise the same TypeScript boundary used by the result UI against real reports.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const cache = new Map();
function compiledUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  if (url.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(url, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(url, "utf8"), {
    fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const dependency = name.startsWith(".")
      ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, url)) : import.meta.resolve(name);
    return `from ${JSON.stringify(dependency)}`;
  });
  const result = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(url.href, result);
  return result;
}

const directory = path.resolve(process.argv[2] ?? "../reports/scoring-refactor-20261108");
const assessmentDirectory = process.argv[3] ?? "assessments";
const outputName = process.argv[4] ?? "frontend-validation.json";
const inventory = JSON.parse(fs.readFileSync(path.join(directory, "video-inventory.json"), "utf8"));
const { validatedTennisAssessment } = await import(compiledUrl(new URL("../app/lib/tennis-assessment.ts", import.meta.url)));
const results = [];
for (const video of inventory) {
  const file = path.join(directory, assessmentDirectory, `${path.parse(video.name).name}.json`);
  if (!fs.existsSync(file)) { results.push({ name: video.name, passed: false, error: "assessment_missing" }); continue; }
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const context = { jobId: raw.source_run_id, videoSha256: video.sha256, durationMs: Math.ceil(video.duration_s * 1000) };
  let error = null;
  const parsed = validatedTennisAssessment(raw, context);
  try {
    assert.ok(parsed, "UI rejected the backend report");
    assert.equal(parsed.overall_score, raw.overall_score);
    assert.ok(Number.isFinite(parsed.overall_score));
    assert.equal(parsed.episodes.length, raw.episodes.length);
    for (let i = 0; i < raw.episodes.length; i++) {
      if (raw.episodes[i].joint_kinematics) {
        assert.deepEqual(parsed.episodes[i].joint_kinematics, raw.episodes[i].joint_kinematics,
          `UI lost joint kinematics for episode ${i}`);
      }
    }
    if (raw.level_assessments) assert.deepEqual(parsed.level_assessments, raw.level_assessments, "UI lost level assessments");
  } catch (failure) { error = failure.message.split("\n")[0]; }
  results.push({ name: video.name, passed: error === null, error,
    score: parsed?.overall_score ?? null, episodes: parsed?.episodes?.length ?? 0,
    joint_kinematics_preserved: parsed?.episodes?.filter(episode => episode.joint_kinematics).length ?? 0,
    level_assessments_preserved: Boolean(parsed?.level_assessments) });
}
const output = { evaluated_at: new Date().toISOString(), validator: "validatedTennisAssessment", results,
  all_passed: results.length > 0 && results.every(result => result.passed) };
fs.writeFileSync(path.join(directory, outputName), JSON.stringify(output, null, 2));
console.log(JSON.stringify(output, null, 2));
if (!output.all_passed) process.exitCode = 1;
