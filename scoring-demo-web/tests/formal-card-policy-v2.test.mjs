import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { gunzipSync } from "node:zlib";
import test from "node:test";
import ts from "typescript";

const cache = new Map();
function compiledUrl(fileUrl) {
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(`${name}.ts`, fileUrl)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { formalConditionScore, isFormalScoringPolicy, formalScoringPolicyVersion } = await import(compiledUrl(new URL("../app/lib/formal-card-scoring-policy.ts", import.meta.url)));
const root = fileURLToPath(new URL("../../", import.meta.url));
const legacy = "automatic-formal-card-scoring-v1.0.0";
const full = JSON.parse(gunzipSync(fs.readFileSync(new URL("./fixtures/micro-cycle-native-e420-unified.json.gz", import.meta.url))));

test("Python fresh policy and browser score arithmetic agree for all zero-to-four condition counts", () => {
  const program = `import json
from tests.test_card_formal_policy_v2 import internal
from rallymate_scoring.card_scoring_policy import apply_scoring_policy
print(json.dumps([dict(n=n,k=k,result=apply_scoring_policy(internal(k,n))) for n in range(5) for k in range(n+1)]))`;
  const child = spawnSync(process.env.PYTHON || "python", ["-c", program], { cwd: root, encoding: "utf8",
    env: { ...process.env, PYTHONPATH: [path.join(root, "src"), path.join(root, "tests")].join(path.delimiter) } });
  assert.equal(child.status, 0, child.stderr);
  for (const { n, k, result } of JSON.parse(child.stdout)) {
    assert.equal(result.scoring_policy_version, formalScoringPolicyVersion);
    assert.equal(formalConditionScore(formalScoringPolicyVersion, k, n, result.grade ?? "E"), result.score);
    assert.equal(result.score, n ? 100*k/n : null);
  }
  assert.equal(formalConditionScore(formalScoringPolicyVersion, 0, 4, "E"), 0);
  for (const [k,n] of [[1,0],[-1,2],[3,2],[1.5,3],[1,NaN]]) assert.equal(formalConditionScore(formalScoringPolicyVersion,k,n,"C"),null);
  assert.equal(isFormalScoringPolicy("unknown"), false);
});

test("actual historical unified report retains v1 scores and policy without migration", () => {
  const old = cardExecutionOf(full);
  assert.equal(old.scoring_policy_version, legacy);
  for (const id of ["FS05-M02", "FS05-M03", "FS05-M04"]) {
    const row = old.cards.find(c => c.indicator_id === id).instances[0];
    assert.equal(row.score, 60); assert.equal(row.grade,"C"); assert.equal(row.scoring_policy_version,legacy);
  }
  assert.deepEqual(cardExecutionOf(old), old);
});

test("changing only historical policy labels cannot make the old bucket score valid in v2", () => {
  const raw = structuredClone(full);
  raw.cards = raw.cards.filter(c => ["FS05-M02","FS05-M03","FS05-M04"].includes(c.indicator_id));
  raw.scoring_policy_version = formalScoringPolicyVersion;
  for (const card of raw.cards) for (const row of card.instances) row.scoring_policy_version = formalScoringPolicyVersion;
  const result = cardExecutionOf(raw);
  assert.equal(result.overall_score, null);
  for (const card of result.cards) assert.equal(card.instances[0].score,null);
});

test("fresh physical-source report can be independently read when supplied by the replay", { skip: !process.env.FORMAL_V2_REPORT }, () => {
  const raw = JSON.parse(fs.readFileSync(process.env.FORMAL_V2_REPORT,"utf8"));
  assert.equal(raw.scoring_policy_version,formalScoringPolicyVersion);
  const parsed = cardExecutionOf(raw);
  assert.equal(parsed.summary.formal_scored_instances,raw.summary.formal_scored_instances);
  assert.equal(parsed.summary.formal_scored_cards,raw.summary.formal_scored_cards);
  assert.ok(Math.abs(parsed.overall_score-raw.overall_score)<1e-12);
  for (const card of parsed.cards) for (const row of card.instances) if (row.status==="scored") {
    assert.equal(row.score,100*row.conditions.filter(c=>c.passed).length/row.conditions.length);
  }
  assert.deepEqual(cardExecutionOf(parsed),parsed);
});
