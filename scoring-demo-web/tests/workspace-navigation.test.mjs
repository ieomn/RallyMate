import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/workspace-navigation.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const {
  readWorkspaceLocation, workspaceHref, reportDateOf, formatReportDate,
  reportVideoDurationMs, formatVideoDuration,
} = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const jobId = "a1ddb051-9aaf-4809-a006-22ad955b4f8b";

test("empty locations start at overview while legacy job links open analysis", () => {
  assert.deepEqual(readWorkspaceLocation(""), { view: "overview", jobId: null });
  assert.deepEqual(readWorkspaceLocation("?"), { view: "overview", jobId: null });
  assert.deepEqual(readWorkspaceLocation(`?job=${jobId}`), { view: "analysis", jobId });
});

test("every explicit view wins over a job query, without deciding to resume it", () => {
  for (const view of ["overview", "sessions", "analysis", "guide"]) {
    assert.deepEqual(readWorkspaceLocation(`?view=${view}&job=${jobId}`), { view, jobId });
  }
});

test("invalid views use valid legacy job navigation and invalid jobs cannot become links", () => {
  assert.deepEqual(readWorkspaceLocation(`?view=other&job=${jobId}`), { view: "analysis", jobId });
  for (const value of ["", "short", "../12345678", "https://example.test/12345678", "x".repeat(81)]) {
    assert.deepEqual(readWorkspaceLocation(`?view=other&job=${encodeURIComponent(value)}`), { view: "overview", jobId: null });
    assert.equal(workspaceHref("analysis", value), "/?view=analysis");
  }
});

test("leaving analysis removes job deep links and overview remains stable on refresh", () => {
  for (const view of ["overview", "sessions", "guide"]) {
    const href = workspaceHref(view, jobId);
    assert.equal(href, `/?view=${view}`);
    assert.deepEqual(readWorkspaceLocation(new URL(href, "https://example.test").search), { view, jobId: null });
  }
  assert.equal(workspaceHref("analysis", jobId), `/?view=analysis&job=${jobId}`);
  assert.equal(workspaceHref("analysis"), "/?view=analysis");
});

test("report date uses completion or submission with an honest label", () => {
  assert.deepEqual(reportDateOf({ completed_at: "2026-10-04T01:02:03Z", created_at: "2026-10-03T01:02:03Z" }), {
    iso: "2026-10-04T01:02:03.000Z", label: "分析完成",
  });
  assert.deepEqual(reportDateOf({ completed_at: "invalid", createdAt: "2026-10-04T08:02:03+08:00" }), {
    iso: "2026-10-04T00:02:03.000Z", label: "提交时间",
  });
  assert.deepEqual(reportDateOf({ completedAt: "2026-10-04T01:02:03Z" }), {
    iso: "2026-10-04T01:02:03.000Z", label: "分析完成",
  });
});

test("viewing or exporting a report cannot invent a training date", () => {
  for (const input of [null, [], {}, { viewedAt: "2026-10-04T01:02:03Z" }, { visitedAt: "2026-10-04T01:02:03Z" }, { exportedAt: "2026-10-04T01:02:03Z" }, { created_at: "2026-10-04" }, { completed_at: 1791061323000 }]) {
    assert.equal(reportDateOf(input), null);
  }
  assert.equal(formatReportDate("2026-10-03T20:02:03Z"), "2026/10/04");
  assert.equal(formatReportDate("invalid"), "日期未提供");
  assert.equal(formatReportDate(null), "日期未提供");
});

test("video duration accepts actual metadata without inferring from model runtime or frame counts", () => {
  assert.equal(reportVideoDurationMs({ input: { video: { duration_ms: 48520 } } }), 48520);
  assert.equal(reportVideoDurationMs({ input: { metadata: { duration_ms: 60000 } } }), 60000);
  assert.equal(reportVideoDurationMs({ input: { video: { duration_ms: 0 } } }), 0);
  for (const input of [null, {}, { processing: { elapsed_seconds: 180 } }, { input: { video: { frame_count: 1800, fps: 30 } } }, { input: { video: { duration_ms: "60000" } } }, { input: { video: { duration_ms: -1 } } }]) {
    assert.equal(reportVideoDurationMs(input), null);
  }
});

test("duration presentation is deterministic and preserves unknown durations", () => {
  assert.equal(formatVideoDuration(0), "0:00");
  assert.equal(formatVideoDuration(48520), "0:48");
  assert.equal(formatVideoDuration(60000), "1:00");
  assert.equal(formatVideoDuration(3661000), "1:01:01");
  for (const value of [undefined, null, "60000", false, NaN, Infinity, -1]) {
    assert.equal(formatVideoDuration(value), "时长未提供");
  }
});
