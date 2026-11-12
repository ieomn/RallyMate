import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

function compiledUrl(file) {
  const source = fs.readFileSync(file, "utf8");
  let js = file.pathname.endsWith(".json") ? `export default ${source}` : ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_, name) => `from ${JSON.stringify(compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)))}`);
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { gradeLevel } = await import(compiledUrl(new URL("../app/scoring/level-grading.ts", import.meta.url)));
const source = JSON.parse(fs.readFileSync(new URL("../../tests/fixtures/source-level-weights.json", import.meta.url), "utf8"));

test("all source table weights reach frontend calculations for both backhand styles", () => {
  for (const targetLevel of [1, 2, 3, 4, 5]) for (const backhandStyle of ["one_handed", "two_handed"]) {
    const result = gradeLevel({}, { targetLevel, backhandStyle });
    const excluded = backhandStyle === "one_handed" ? ["GS02", "RS02"] : ["GS03", "RS03"];
    for (const row of source.rows.filter(row => row.level === targetLevel)) {
      if (row.kind === "module") {
        row.codes.forEach((code, index) => assert.equal(result.domains[code].module_weight, row.percent[index] / 100));
      } else {
        const active = row.codes.map((code, index) => [code, row.percent[index]])
          .filter(([code, weight]) => !excluded.includes(code) && weight > 0);
        const total = active.reduce((sum, [, weight]) => sum + weight, 0);
        assert.deepEqual(result.domains[row.kind].active_weights, Object.fromEntries(active.map(([code, weight]) => [code, weight / total])), `table ${row.table}, ${backhandStyle}`);
      }
    }
  }
});

test("source policy distinguishes a zero score from an inactive or missing technique", () => {
  const beginner = gradeLevel({ GS01: 100, GS02: 0, GS03: 100, GS04: 50, GS05: 50, FS09: 100 });
  assert.equal(beginner.overall_score, 55);
  assert.equal(beginner.weighted_coverage, .7);
  assert.equal(beginner.policy_version, "level-grading-policy-v2.0.0");
  const inactive = gradeLevel({ SS02: 100, NS03: 100, FS09: 100 }, { targetLevel: 2 });
  assert.equal(inactive.overall_score, null);
  const zero = gradeLevel({ SS01: 100, SS02: 0, SS03: 0, SS04: 0, SS05: 0 }, { targetLevel: 5 });
  const missing = gradeLevel({ SS01: 100, SS02: null }, { targetLevel: 5 });
  assert.equal(zero.overall_score, 20);
  assert.equal(missing.overall_score, 100);
  assert.equal(missing.domains.SS.coverage, .2);
});
