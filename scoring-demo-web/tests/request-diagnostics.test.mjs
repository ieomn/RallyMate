import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/request-diagnostics.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { reportReadStage, safeRequestDiagnostic, safeRequestId, attachRequestDiagnostics, getRequestDiagnostics } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const row = () => ({ operation: "report", stage: "body", outcome: "failure", category: "body_read", requestId: "f67b39de-cf4d-4298-b175-fbc5a0c094f1", httpStatus: 200, startedAt: "2026-10-19T00:00:00.000Z", elapsedMs: 25, bodyComplete: false });

test("report loading stages distinguish preparation, transfer, decoding and failure without claiming a percentage", () => {
  assert.equal(reportReadStage(), "正在连接报告服务");
  const success = { ...row(), outcome: "success", category: null };
  for (const stage of ["headers", "body", "json"]) assert.equal(reportReadStage({ ...success, httpStatus: 202, stage }), "服务器正在整理报告");
  assert.deepEqual(["headers", "body", "json", "decode"].map(stage => reportReadStage({ ...success, stage })),
    ["正在接收报告数据", "正在解析报告数据", "正在校验并解码报告证据", "报告主体已读取"]);
  assert.equal(reportReadStage(row()), "本次报告读取未成功");
});

test("safe diagnostics rebuild only fixed fields and reject coercion, getters and unsafe IDs", () => {
  const value = row();
  const output = safeRequestDiagnostic({ ...value, url: "SECRET", payload: "SECRET", message: "SECRET", cause: "SECRET", headers: { authorization: "SECRET" } });
  assert.deepEqual(output, value); assert.ok(Object.isFrozen(output));
  let touched = false;
  for (const key of ["operation", "stage", "outcome", "category"]) {
    assert.equal(safeRequestDiagnostic({ ...value, [key]: { toString() { touched = true; return value[key]; } } }), null);
  }
  const getter = { ...value };
  Object.defineProperty(getter, "operation", { get() { touched = true; return "report"; } });
  assert.equal(safeRequestDiagnostic(getter), null); assert.equal(touched, false);
  for (const requestId of ["token_secret", "Bearer secret", value.requestId + "\n", "https://private.invalid", "a".repeat(129)]) {
    assert.equal(safeRequestId(requestId), null); assert.equal(safeRequestDiagnostic({ ...value, requestId }), null);
  }
  for (const patch of [{ elapsedMs: Infinity }, { elapsedMs: -1 }, { httpStatus: 600 }, { httpStatus: "200" }, { startedAt: value.startedAt + "\n" }, { bodyComplete: "true" }, { stage: "private" }]) assert.equal(safeRequestDiagnostic({ ...value, ...patch }), null);
  assert.equal(safeRequestId("web-mgj31234-ab123456"), "web-mgj31234-ab123456");
});

test("diagnostics follow bounded causes without exposing payload, text, cycles or cause getters", () => {
  const original = Object.assign(new Error("PRIVATE_CAUSE"), { payload: { secret: true } });
  attachRequestDiagnostics(original, [row()]);
  const wrapped = new Error("read failed", { cause: original });
  assert.deepEqual(getRequestDiagnostics(wrapped), [row()]);
  assert.ok(!JSON.stringify(getRequestDiagnostics(wrapped)).includes("PRIVATE"));
  assert.equal(wrapped.cause, original);
  assert.ok(Object.isFrozen(getRequestDiagnostics(wrapped)));
  const cycle = new Error("cycle"); cycle.cause = cycle;
  assert.deepEqual(getRequestDiagnostics(cycle), []);
  let touched = false;
  assert.deepEqual(getRequestDiagnostics(Object.defineProperty({}, "cause", { get() { touched = true; return original; } })), []);
  assert.equal(touched, false);
  let deep = original;
  for (let i = 0; i < 9; i++) deep = new Error("nested", { cause: deep });
  assert.deepEqual(getRequestDiagnostics(deep), []);
  attachRequestDiagnostics(original, Array.from({ length: 30 }, row));
  assert.equal(getRequestDiagnostics(original).length, 8);
});
