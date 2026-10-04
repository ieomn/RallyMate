import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/trajectory-viewer.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { trajectoryEdges, trajectoryWindow, timestampForVideoTime, videoTimeForTimestamp, visibleTrajectoryPaths, trajectoryAvailability, trajectoryDisplayOpacity } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const p = (timestamp_ms, x, source = "observed", confidence = .9) => ({ timestamp_ms, x, y: .5, source, confidence });

test("trajectory edges never join distinct segments or long missing gaps", () => {
  const edges = trajectoryEdges([[p(0, .1), p(40, .2), p(700, .7)], [p(710, .3), p(750, .4)]]);
  assert.equal(edges.length, 2);
  assert.deepEqual(edges.map((edge) => [edge.from.timestamp_ms, edge.to.timestamp_ms]), [[0, 40], [710, 750]]);
});

test("short-gap interpolation is displayed separately from observations", () => {
  const edges = trajectoryEdges([[p(0, .1), p(40, .2, "interpolated"), p(80, .3), p(120, .4)]], 100);
  assert.equal(edges.length, 2);
  assert.deepEqual(edges.map((edge) => edge.source), ["interpolated", "interpolated"]);
});

test("video synchronization uses millisecond origin without shifting original uploads", () => {
  assert.equal(timestampForVideoTime(2.25, 5000), 7250);
  assert.equal(videoTimeForTimestamp(7250, 5000), 2.25);
  assert.equal(videoTimeForTimestamp(7250), 7.25);
  assert.equal(videoTimeForTimestamp(100, 5000), 0);
});

test("playback shows only its recent tail and keeps parallel tracks independent", () => {
  const paths = [[p(0, .1), p(1000, .2), p(2900, .4), p(3100, .5)], [p(2900, .8), p(2950, .9)]];
  const window = trajectoryWindow(paths, 3000);
  assert.deepEqual(window.map(path => path.map(point => point.timestamp_ms)), [[2900], [2900, 2950]]);
  assert.equal(trajectoryEdges(window, 3000).length, 1);
  assert.equal(paths[0].length, 4, "source evidence is not modified by seeking");
});

test("display hides static tracks, low confidence observations and future points", () => {
  const staticTrack = [p(880, .3), p(920, .301), p(960, .3)];
  const lowConfidence = [p(880, .1, "observed", .3), p(920, .2, "observed", .4)];
  const movingTrack = [p(840, .5), p(880, .55), p(920, .6), p(960, .65), p(1040, .7)];
  const paths = [staticTrack, lowConfidence, movingTrack];
  const result = visibleTrajectoryPaths(paths, 1000);
  assert.deepEqual(result, [movingTrack.slice(0, 4)]);
  assert.equal(movingTrack.length, 5, "display filtering must not erase inference evidence");
});

test("invalid points, omitted interpolation and abrupt jumps never become connecting lines", () => {
  assert.deepEqual(visibleTrajectoryPaths([[p(800, .1), p(840, .15, "observed", .2), p(880, .2)]], 900), []);
  assert.deepEqual(visibleTrajectoryPaths([[p(800, .1), p(840, .15, "interpolated"), p(880, .2)]], 900, { showInterpolated: false }), []);
  assert.equal(visibleTrajectoryPaths([[p(800, .1), p(840, .15, "interpolated"), p(880, .2)]], 900, { showInterpolated: true })[0].length, 3);
  assert.deepEqual(visibleTrajectoryPaths([[p(800, .1), p(840, .9)]], 900), []);
  assert.deepEqual(visibleTrajectoryPaths([[p(800, -.1), p(840, .1)]], 900), []);
});

test("recent simultaneous candidates stay separate and stale flights expire after 600 ms", () => {
  const paths = [[p(840, .1), p(880, .2)], [p(900, .5), p(940, .6)]];
  assert.deepEqual(visibleTrajectoryPaths(paths, 1000), [paths[1], paths[0]]);
  assert.deepEqual(visibleTrajectoryPaths(paths, 1200), [paths[1], paths[0]]);
  assert.deepEqual(visibleTrajectoryPaths(paths, 1481), [paths[1]]);
  assert.deepEqual(visibleTrajectoryPaths(paths, 1540), [paths[1]]);
  assert.deepEqual(visibleTrajectoryPaths(paths, 1541), []);
  assert.deepEqual(visibleTrajectoryPaths(paths, 2000, { mode: "tail" }), []);
  assert.deepEqual(visibleTrajectoryPaths(paths, 2000, { mode: "overview" }), [paths[1], paths[0]]);
  assert.deepEqual(trajectoryEdges(visibleTrajectoryPaths(paths, 1000)).map(edge => [edge.from.x, edge.to.x]), [[.5, .6], [.1, .2]]);
});

test("trusted dense reconstruction survives low detector scores and sampled timestamp gaps", () => {
  const path = [p(0, .1, "observed", .2), p(1000, .3, "observed", .25), p(2000, .6, "observed", .22), p(3000, .7, "observed", .2)];
  const shown = visibleTrajectoryPaths([path], 2500, { trustedSegments: true });
  assert.deepEqual(shown, [path.slice(0, 3)]);
  const edges = trajectoryEdges(shown, 2500, Infinity, [[{ start_ms: 1300, end_ms: 1600 }]]);
  assert.deepEqual(edges.map(edge => edge.source), ["observed", "interpolated"]);
  assert.equal(path.length, 4, "display must retain source evidence unchanged");
});

test("trusted geometry cannot bypass missing, nonfinite or below-floor confidence", () => {
  for (const confidence of [undefined, NaN, Infinity, -.1, .01, 1.1]) {
    const path = [p(0, .1, "observed", .3), { ...p(100, .15), confidence }, p(200, .2, "observed", .3)];
    assert.deepEqual(visibleTrajectoryPaths([path], 200, { trustedSegments: true }), []);
  }
  const supported = [p(0, .1, "observed", .15), p(100, .15, "interpolated", .1275), p(200, .2, "observed", .15)];
  assert.deepEqual(visibleTrajectoryPaths([supported], 200, { trustedSegments: true }), [supported]);
});

test("point sampling does not expire a source-supported segment between samples or extend it past its end", () => {
  const path = [p(0, .1), p(1000, .2), p(2000, .3), p(3000, .4)];
  const options = { trustedSegments: true, sampledPathEndMs: [3000] };
  assert.deepEqual(visibleTrajectoryPaths([path], 2900, options), [path.slice(0, 3)]);
  assert.deepEqual(visibleTrajectoryPaths([path], 3600, options), [path]);
  assert.deepEqual(visibleTrajectoryPaths([path], 3601, options), []);
  assert.deepEqual(visibleTrajectoryPaths([path], 0, options), [], "do not reveal future points while seeking backwards");
  assert.deepEqual(visibleTrajectoryPaths([path], 1100, options), [path.slice(0, 2)]);
});

test("display strength distinguishes detector confidence without treating it as accuracy", () => {
  assert.ok(trajectoryDisplayOpacity([p(0, .1, "observed", .2), p(100, .2, "observed", .3)]) < trajectoryDisplayOpacity([p(0, .1), p(100, .2)]));
  const sourceBased = trajectoryDisplayOpacity([p(0, .1), p(100, .2)], .2);
  assert.equal(sourceBased, trajectoryDisplayOpacity([p(0, .1, "observed", .2), p(100, .2, "observed", .2)]));
  assert.equal(trajectoryDisplayOpacity([p(0, .1, "interpolated", .9)]), .2, "interpolation cannot inflate detection support");
});

test("interpolation provenance cannot bleed into another simultaneous ball path", () => {
  const paths = [[p(0, .1), p(1000, .3)], [p(0, .7), p(1000, .5)]];
  const edges = trajectoryEdges(paths, 1000, Infinity, [[], [{ start_ms: 100, end_ms: 800 }]]);
  assert.deepEqual(edges.map(edge => edge.source), ["observed", "interpolated"]);
});

test("overview caps separate past flights without connecting them or drawing future points", () => {
  const paths = Array.from({ length: 20 }, (_, i) => [p(i * 1000, .1), p(i * 1000 + 100, .2)]);
  const shown = visibleTrajectoryPaths(paths, 15000, { mode: "overview", trustedSegments: true });
  assert.equal(shown.length, 12);
  assert.equal(trajectoryEdges(shown, 15000, Infinity).length, 12);
  assert.ok(shown.flat().every(point => point.timestamp_ms <= 15000));
});

test("missing trajectory distinguishes pending, failed and unavailable results", () => {
  assert.equal(trajectoryAvailability(null, true).state, "pending");
  assert.equal(trajectoryAvailability(null, false, "502").state, "error");
  assert.equal(trajectoryAvailability(null, false).state, "unavailable");
  const empty = { status: "not_observed", source: {}, ball: { observed: [] } };
  assert.equal(trajectoryAvailability(empty).state, "not_observed");
  assert.doesNotMatch(trajectoryAvailability(empty).description, /等待/);
  assert.equal(trajectoryAvailability({ ...empty, status: "unsupported" }).state, "unsupported");
});
