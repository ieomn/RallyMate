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

test("unknown motion gets a usable general drill, not a forehand diagnosis", () => {
  const row = first();
  assert.match(row.nextStep, /通用练习（并非已诊断的缺陷）/);
  assert.match(row.nextStep, /动作类型待确认/);
  assert.match(row.nextStep, /小场地.*低速球.*目标区.*2 组.*5 球/);
  assert.match(row.nextStep, /这组只关注/);
  assert.match(row.nextStep, /同样来球和目标复测 10 球/);
  assert.doesNotMatch(row.nextStep, /你的正手|正手存在|需要增加转体|最大屈膝|强拧手腕/);
  assert.equal(row.evidenceState, "observation");
  assert.match(row.limitation, /不能单独判定技术好坏/);
});

test("known stroke candidates select conditional matching demonstrations without changing evidence", () => {
  for (const [label, name, source] of [["forehand", "正手", "forehand"], ["two_handed_backhand", "双手反手", "backhand"], ["backhand", "单手反手", "backhand"]]) {
    const row = first(report(), motion(label));
    assert.match(row.nextStep, new RegExp(`若原片确认是${name}`));
    assert.match(row.nextStep, new RegExp(`熟悉的${name}握拍`));
    assert.ok(row.source_links.some(item => item.url.endsWith(`learning-the-basics--${source}.html`)));
    assert.match(row.nextStep, /记录落入目标区的次数/);
    assert.equal(row.evidenceState, "observation");
  }
});

test("serve practice is toss-first and does not prescribe a groundstroke or forced wrist action", () => {
  const row = first(report(), motion("serve_motion"));
  assert.match(row.nextStep, /若原片确认是在练发球/);
  assert.match(row.nextStep, /先不挥拍.*2 组.*5 次抛球/);
  assert.match(row.nextStep, /同一站位再做 10 次/);
  assert.doesNotMatch(row.nextStep, /小场地|正手|反手|甩腕|压腕|强拧|最大/);
  assert.equal(row.source_links[0].url, "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-serve-for-beginners/");
});

test("unclassified, unrelated and conflicting episodes never borrow a stroke label", () => {
  const mixed = motion("forehand");
  mixed.families.baseline.episodes.push(motion("backhand").families.baseline.episodes[0]);
  for (const data of [motion("forehand", { classification: { label: "forehand", status: "unclassified", label_zh: "正手？" } }), motion("forehand", { start_ms: 2500, end_ms: 3500 }), mixed, motion("return_motion")]) {
    assert.match(first(report(), data).nextStep, /动作类型待确认/);
  }
});

test("footwork practice has one recovery target and same-task retest without penalizing open stance", () => {
  const row = first(report({ event: "FS02", feature: "stance_width_body" }));
  assert.match(row.nextStep, /这组只关注“回到标记等下一球”/);
  assert.match(row.nextStep, /不追求站死不动/);
  assert.match(row.nextStep, /同样喂球与标记复测 10 球/);
  assert.doesNotMatch(row.nextStep, /站距不足|开放式站姿错误|站距必须|屈膝不足/);
});

test("no measurement adds practice and capture instructions without converting missingness to a defect", () => {
  const rows = trainingGuidance(report({ measured: false }));
  assert.ok(rows.length > 0);
  for (const row of rows) {
    assert.equal(row.evidenceState, "unavailable");
    assert.match(row.nextStep, /通用练习.*复测.*复拍时/);
    assert.match(row.limitation, /缺少证据不代表动作没有发生/);
    assert.match(row.nextStep, /起步安排/);
  }
  assert.deepEqual(trainingGuidance(null), []);
});

test("only allowlisted static official links are returned and callers cannot mutate later suggestions", () => {
  const permitted = new Set([
    "https://www.usta.com/content/dam/usta/content-fragments/tennis-service-center/assets/pdfs/USTA-Tennis-101-for-Adults.pdf",
    "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--forehand.html",
    "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--backhand.html",
    "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-serve-for-beginners/",
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
