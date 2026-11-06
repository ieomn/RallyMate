import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  let js = ts.transpileModule(fs.readFileSync(url, "utf8"), { fileName: url.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, url);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, url)) : import.meta.resolve(name))}`;
  });
  const value = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(url.href, value); return value;
}
const { parseShotStatistics, shotStatisticsCount, formatShotTime, shotCourtPoint, shotCourtDisplay, shotSeekDetail } = await import(compiledUrl(new URL("../app/lib/shot-statistics.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/ShotStatisticsPanel.tsx", import.meta.url)));
const sha = "a".repeat(64);
const event = (id, kind, status = "candidate", time = 1000) => ({ id, kind, status, timestamp_ms: time,
  frame_index: 30, position_image: { x: .4, y: .5 }, position_court: null, source: "trajectory_rule", reason_codes: ["trajectory_turn"] });
const report = (events = [], status = "unreviewed", revision = 0) => ({ schema_version: "1.0.0", job_id: "current-job", status, revision,
  source: { video_sha256: sha, frame_count: 301, start_ms: 0, end_ms: 10000, duration_ms: 10000, coordinate_space: "normalized_frame_0_1" },
  summary: { confirmed_hit_count: events.filter(e => e.kind === "hit" && e.status === "confirmed").length,
    confirmed_bounce_count: events.filter(e => e.kind === "bounce" && e.status === "confirmed").length,
    mapped_bounce_count: events.filter(e => e.kind === "bounce" && e.status === "confirmed" && e.position_court).length,
    candidate_hit_count: events.filter(e => e.kind === "hit" && e.status === "candidate").length,
    candidate_bounce_count: events.filter(e => e.kind === "bounce" && e.status === "candidate").length,
    dismissed_count: events.filter(e => e.status === "dismissed").length,
    count_semantics: "review_confirmed_events_not_total_video" }, events, limitations_zh: ["自动候选需要回看确认。"] });
const parse = value => parseShotStatistics(value, "current-job", sha);
const render = (data, extra = {}) => renderToStaticMarkup(createElement(Panel, { data, jobId: "current-job", onReview() {}, ...extra }));

test("only the current job and source video may populate shot statistics", () => {
  const value = report([event("hit-1", "hit")]), snapshot = structuredClone(value);
  assert.ok(parse(value)); assert.deepEqual(value, snapshot);
  assert.equal(parse({ ...value, job_id: "another-job" }), null);
  assert.equal(parse({ ...value, source: { ...value.source, video_sha256: "b".repeat(64) } }), null);
  assert.equal(parse({ ...value, source: { ...value.source, coordinate_space: "pixels" } }), null);
  assert.ok(parseShotStatistics(value, "current-job", sha.toUpperCase()));
});

test("candidate and empty evidence never become a claimed zero-shot video", () => {
  const data = parse(report([event("hit-1", "hit"), event("bounce-1", "bounce")]));
  assert.equal(shotStatisticsCount(data, "hit"), null);
  assert.equal(shotStatisticsCount(data, "bounce"), null);
  assert.equal(shotStatisticsCount(parse(report([], "partial")), "hit"), null);
  assert.equal(shotStatisticsCount(null, "hit"), null);
  const html = render(data);
  assert.match(html, /已确认击球<\/span><strong>—<\/strong>/);
  assert.match(html, /候选不计入次数/);
  assert.match(html, /待确认 2/);
  assert.doesNotMatch(html, /class="shot-statistics-dot/);
});

test("reviewed counts include only confirmed events, never dismissed or candidates", () => {
  const data = parse(report([event("hit-1", "hit", "confirmed"), event("hit-2", "hit"), event("hit-3", "hit", "dismissed")], "reviewed", 2));
  assert.equal(shotStatisticsCount(data, "hit"), 1);
  assert.equal(shotStatisticsCount(data, "bounce"), 0);
  const html = render(data);
  assert.match(html, /已确认击球<\/span><strong>1<\/strong>/);
  assert.match(html, /只统计已确认事件，不等同于全片总次数/);
  assert.match(html, /撤回确认/);
  assert.doesNotMatch(html, /data-shot-event-id="hit-3"/);
});

test("duplicate events or forged summary counts are rejected", () => {
  const one = event("same", "hit");
  assert.equal(parse(report([one, { ...one }])), null);
  const value = report([one]); value.summary.confirmed_hit_count = 9;
  assert.equal(parse(value), null);
  assert.equal(parse({ ...report(), summary: { ...report().summary, count_semantics: "detected_ball_points" } }), null);
});

test("invalid timestamps, nonfinite coordinates and unsupported origins fail closed", () => {
  for (const change of [{ timestamp_ms: -1 }, { timestamp_ms: 10001 }, { timestamp_ms: NaN }, { frame_index: -1 },
    { position_image: { x: NaN, y: .5 } }, { position_image: { x: 1.1, y: .5 } }, { source: "airborne_projection" }]) {
    assert.equal(parse(report([{ ...event("hit", "hit"), ...change }])), null, JSON.stringify(change));
  }
  for (const change of [{ revision: -1 }, { status: "done" }, { source: { ...report().source, duration_ms: Infinity } }]) {
    assert.equal(parse({ ...report(), ...change }), null);
  }
});

test("only confirmed bounces carry court positions; outside points remain outside", () => {
  const bounce = { ...event("bounce", "bounce", "confirmed"), position_court: { x: -.2, y: 1.1, source: "manual" } };
  const data = parse(report([bounce], "reviewed", 1));
  assert.ok(data);
  assert.equal(data.events[0].position_court.x, -.2);
  assert.deepEqual(shotCourtDisplay(bounce.position_court), { left: "15%", top: "90%" });
  for (const change of [{ kind: "hit" }, { status: "candidate" }, { status: "dismissed" },
    { position_court: { x: -1, y: .5, source: "manual" } }, { position_court: { x: .5, y: .5, source: "ball_trajectory" } }]) {
    assert.equal(parse(report([{ ...bounce, ...change }], "reviewed", 1)), null, JSON.stringify(change));
  }
});

test("court display distinguishes manual and calibrated positions and links only confirmed bounces", () => {
  const manual = { ...event("manual", "bounce", "confirmed"), source: "manual", position_court: { x: .2, y: .8, source: "manual" } };
  const mapped = { ...event("mapped", "bounce", "confirmed", 2000), position_court: { x: .7, y: .3, source: "calibrated_ground_projection" } };
  const data = parse(report([manual, mapped, event("candidate", "bounce")], "reviewed", 2));
  const html = render(data);
  assert.equal((html.match(/class="shot-statistics-dot/g) ?? []).length, 2);
  assert.match(html, /is-calibrated/); assert.match(html, /人工标记/); assert.match(html, /标定映射/);
  assert.match(html, /删除记录/); assert.match(html, /确认并标点/);
  assert.match(html, /回看 0:02.0 的落地/);
});

test("manual recording requires an available source time and cannot write while saving", () => {
  const data = parse(report());
  assert.match(render(data), /disabled="">记一次击球/);
  assert.match(render(data, { currentTimeMs: 10001 }), /disabled="">标记落点/);
  assert.doesNotMatch(render(data, { currentTimeMs: 1234 }), /disabled="">记一次击球/);
  assert.match(render(data, { currentTimeMs: 1234, saving: true }), /disabled="">记一次击球/);
  assert.match(render(data, { currentTimeMs: 1234 }), /当前画面 0:01.2/);
});

test("a stale job, unavailable ledger, or read error cannot enable editing", () => {
  assert.match(render(report(), { jobId: "new-job", currentTimeMs: 1000 }), /disabled="">记一次击球/);
  assert.match(render(report([], "unavailable"), { currentTimeMs: 1000 }), /disabled="">记一次击球/);
  const html = render(null, { error: "读取失败", onRetry() {} });
  assert.match(html, /role="alert"/); assert.match(html, /重新读取/); assert.doesNotMatch(html, /<strong>0<\/strong>/);
});

test("court selection maps corners and exterior without clamping a miss into the court", () => {
  const bounds = { left: 10, top: 20, width: 200, height: 150 };
  assert.deepEqual(shotCourtPoint(60, 45, bounds), { x: 0, y: 0 });
  assert.deepEqual(shotCourtPoint(160, 145, bounds), { x: 1, y: 1 });
  assert.deepEqual(shotCourtPoint(40, 145, bounds), { x: -.2, y: 1 });
  assert.equal(shotCourtPoint(0, 20, bounds), null);
  assert.equal(shotCourtPoint(60, 45, { ...bounds, width: 0 }), null);
  assert.equal(shotCourtPoint(NaN, 45, bounds), null);
});

test("event seeking carries source job and prefers the original video", () => {
  assert.deepEqual(shotSeekDetail({ timestamp_ms: 1250 }, "current-job"), { timestampMs: 1250, jobId: "current-job", preferOriginal: true });
  assert.equal(formatShotTime(62567), "1:02.5"); assert.equal(formatShotTime(-1), "—");
});

test("layout places sidebar next to video and switches to a lower panel on narrow screens", () => {
  const css = fs.readFileSync(new URL("../app/shot-statistics.css", import.meta.url), "utf8");
  assert.match(css, /\.replay-statistics-layout[^}]*grid-template-columns:minmax\(0,1fr\) 300px/);
  assert.match(css, /@media \(max-width:900px\)[^{]*\{ \.replay-statistics-layout \{ grid-template-columns:1fr/);
  assert.match(css, /shot-statistics-dot\.is-calibrated/);
});
