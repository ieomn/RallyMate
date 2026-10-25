import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const compiled = new Map();
function load(url) {
  if (compiled.has(url.href)) return compiled.get(url.href);
  let js = ts.transpileModule(fs.readFileSync(url, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/import (\w+) from "([^"\n]+\.json)";?/g, (_match, name, path) => `const ${name} = ${fs.readFileSync(new URL(path, url), "utf8")};`);
  js = js.replace(/from "([^"]+)"/g, (_match, path) => `from ${JSON.stringify(path.startsWith(".") ? load(new URL(`${path}.ts`, url)) : import.meta.resolve(path))}`);
  const result = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  compiled.set(url.href, result); return result;
}
const { trainingGuidance } = await import(load(new URL("../app/lib/training-guidance.ts", import.meta.url)));

function report({ event = "GS01", feature = "shoulder_hip_separation_deg", measured = true } = {}) {
  return { job_id: "practice-test", input: { video: { duration_ms: 4000 } }, indicator_assessment: {
    version: "word-indicator-assessment-v1.0.0", source_binding: {},
    indicators: measured ? [{ indicator_id: `${event}-M01${event.startsWith("GS") ? "-01" : ""}`, name_zh: "局部测量", event_code: event, stage_code: event, status: "contextual_measurement", observation_ids: ["obs"], blockers: [], source: {}, grade: {} }] : [],
    observations: measured ? [{ observation_id: "obs", source: "motion_joint_measurement", status: "measured", feature_name: feature, label_zh: "局部测量", value: 35, unit: "deg", start_ms: 1000, end_ms: 1800, statistics: {}, windows: [], scope: "contextual_motion_phase", definition_zh: "二维测量" }] : [],
  } };
}
function motion(label, options = {}) {
  const family = label === "serve_motion" ? "serve" : "baseline";
  return { schema_version: "1.1.0", status: "available", contact_confirmed: false, families: { [family]: { status: "analyzed", episodes: [{ episode_id: "motion", family, start_ms: 500, peak_ms: 1400, end_ms: 2200, classification: { label, status: "rule_inferred", label_zh: "规则候选" }, phases: [], metrics: {}, ...options }] } } };
}
const first = (value = report(), movement = null) => trainingGuidance(value, movement)[0];

test("ungraded observations produce a concrete drill and comparable retest without a diagnosis", () => {
  const row = first(report({ feature: "unmapped_measurement" }));
  assert.match(row.drill, /小场地.*低速球.*目标区.*2 组.*5 球/);
  assert.match(row.retest, /同样来球和目标复测 10 球/);
  assert.ok(row.goal && row.observation);
  assert.doesNotMatch(row.nextStep, /你的正手|正手存在|需要增加转体|最大屈膝|强拧手腕|原文|Word|候选|P0080/);
  assert.equal(row.evidenceState, "observation");
  assert.match(row.limitation, /不据此认定动作缺陷/);
});

test("head height and speed never select arm correction or a swing-speed conclusion", () => {
  for (const feature of ["head_center_speed_body_s", "head_to_shoulders_distance_torso", "head_center_y_body"]) {
    const row = first(report({ feature }));
    assert.match(row.title, /看球/);
    assert.match(row.drill, /抛接慢球/);
    assert.doesNotMatch(row.title + row.goal + row.drill + row.observation, /手臂|挥拍速度|伸肘|甩腕/);
  }
});

test("known stroke labels select a conditional matching drill without asserting a diagnosis", () => {
  for (const [label, name, source] of [["forehand", "正手", "forehand"], ["two_handed_backhand", "双手反手", "backhand"], ["backhand", "单手反手", "backhand"]]) {
    const row = first(report({ feature: "left_elbow_angle_deg" }), motion(label));
    assert.match(row.drill, new RegExp(`如果本段练的是${name}`));
    assert.match(row.drill, new RegExp(`熟悉的${name}握拍`));
    assert.ok(row.source_links.some(item => item.url.endsWith(`learning-the-basics--${source}.html`)));
    assert.match(row.retest, /记录落入目标区的次数/);
    assert.equal(row.evidenceState, "observation");
  }
});

test("serve practice is toss-first with its own retest", () => {
  const row = first(report(), motion("serve_motion"));
  assert.match(row.drill, /如果本段在练发球.*先不挥拍.*2 组.*5 次抛球/);
  assert.match(row.retest, /同一站位再做 10 次/);
  assert.doesNotMatch(row.nextStep, /小场地|正手|反手|甩腕|压腕|强拧|最大/);
  assert.equal(row.source_links[0].url, "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-serve-for-beginners/");
});

test("unclassified, unrelated and conflicting episodes never borrow a stroke label", () => {
  const mixed = motion("forehand");
  mixed.families.baseline.episodes.push(motion("backhand").families.baseline.episodes[0]);
  for (const data of [motion("forehand", { classification: { label: "forehand", status: "unclassified", label_zh: "正手？" } }), motion("forehand", { start_ms: 2500, end_ms: 3500 }), mixed, motion("return_motion")]) {
    assert.doesNotMatch(first(report({ feature: "left_elbow_angle_deg" }), data).nextStep, /正手|反手|发球/);
  }
});

test("footwork keeps a measurable recovery target without prescribing closed stance", () => {
  const row = first(report({ event: "FS02", feature: "stance_width_body" }));
  assert.match(row.drill, /小步调整.*回到标记/);
  assert.match(row.drill, /不要求双脚站死/);
  assert.match(row.retest, /复测 10 球.*次数/);
  assert.doesNotMatch(row.nextStep, /站距不足|开放式站姿错误|站距必须|屈膝不足/);
});

test("no measurement offers a baseline practice and keeps missingness separate from defects", () => {
  const rows = trainingGuidance(report({ measured: false }));
  assert.ok(rows.length > 0);
  for (const row of rows) {
    assert.equal(row.evidenceState, "unavailable");
    assert.match(row.drill, /2 组.*5 球/);
    assert.match(row.retest, /10 球/);
    assert.match(row.limitation, /当前测量不足/);
    assert.match(row.detail, /固定机位/);
  }
  assert.deepEqual(trainingGuidance(null), []);
});

test("forged cached low scores cannot produce a personal shortfall", () => {
  const value = report({ feature: "head_center_speed_body_s" });
  value.card_execution = { version: "card-execution-v1.0.0", source_run_id: value.job_id, scoring_policy_version: "automatic-formal-card-scoring-v2.0.0", overall_score: 0,
    cards: [{ indicator_id: "FS01-M02", event_code: "FS01", instances: [{ status: "scored", score: 0, conditions: [{ metric: "hip_drop_torso", value: 0, threshold: .03, passed: false }] }] }] };
  const row = first(value);
  assert.doesNotMatch(row.observation, /低于|高于|未达/);
  assert.match(row.title, /看球/);
});

test("only allowlisted static official links are returned and callers cannot mutate later suggestions", () => {
  const permitted = new Set([
    "https://www.usta.com/content/dam/usta/content-fragments/tennis-service-center/assets/pdfs/USTA-Tennis-101-for-Adults.pdf",
    "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--forehand.html",
    "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--backhand.html",
    "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-serve-for-beginners/",
    "https://www.lta.org.uk/play/tennis-tips-and-techniques/improve-your-tennis-footwork-on-court-with-these-simple-tips/",
  ]);
  const value = report(); value.source_links = [{ title: "untrusted", url: "javascript:alert(1)" }];
  for (const label of ["unclassified", "forehand", "backhand", "two_handed_backhand", "serve_motion"]) {
    const row = first(value, motion(label));
    assert.ok(row.source_links.length);
    for (const source of row.source_links) assert.ok(permitted.has(source.url));
    row.source_links[0].url = "https://not-official.invalid/";
    assert.ok(permitted.has(first(value, motion(label)).source_links[0].url));
  }
});

test("validated real scored shortfalls drive practice while cross-job or corrupted scores cannot", () => {
  const raw = JSON.parse(fs.readFileSync(new URL("./fixtures/temporal-formal-real-0a3-contract.json", import.meta.url), "utf8")).report;
  const value = { job_id: raw.source_run_id, input: { video: { duration_ms: 1000000 } }, card_execution: raw };
  const rows = trainingGuidance(value);
  assert.ok(rows.some(row => row.id.startsWith("scored:")));
  assert.match(rows.map(row => row.observation).join(""), /低于本项参考/);
  assert.ok(rows.every(row => row.drill && row.retest && row.goal));
  assert.ok(rows.every(row => row.moment));
  assert.doesNotMatch(rows.map(row => row.observation).join(""), /技术错误|屈膝不足|你的动作有问题|原文|Word/);
  assert.ok(trainingGuidance({ ...value, job_id: "different-job" }).every(row => !row.id.startsWith("scored:")));
  const corrupt = structuredClone(value);
  for (const card of corrupt.card_execution.cards) for (const instance of card.instances) instance.score = 0;
  assert.ok(trainingGuidance(corrupt).every(row => !row.id.startsWith("scored:")));
});
