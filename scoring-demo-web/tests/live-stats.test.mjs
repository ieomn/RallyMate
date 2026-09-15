import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/live-stats.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { summarizeLiveStats, buildTrajectoryDisplayModel } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

const trajectory = {
  source: { frame_count: 2911, coordinate_space: "normalized_frame_0_1" },
  ball: { observed: [{ x: .2, y: .3 }], observed_count: 251, coverage_fraction: .0862, confidence: { mean: .601 }, prediction_status: "heuristic_preview", predicted: [{ x: .3, y: .4 }], velocity: { direction_image_deg: -81.6, segment_count: 5 } },
  racket: { observed: [], observed_count: 74 },
};

test("trajectory display keeps observed points separate from heuristic extrapolation", () => {
  const model = buildTrajectoryDisplayModel(trajectory);
  assert.equal(model.observedCount, 251);
  assert.equal(model.predictedCount, 1);
  assert.equal(model.predictionLabel, "启发式外推");
  assert.match(model.predictionNote, /不代表真实落点/);
});

test("stats never infer a hit count from detections or action segments", () => {
  const stats = summarizeLiveStats({ actions: [{ event_code: "FS01", name_zh: "分腿垫步", detected_segments: 33 }] }, trajectory, {
    counts: { detections: { ball: 1519, racket: 1527 }, frames_with_class: { ball: 1245, racket: 1216 } },
    processing: { processed_frames: 2911 },
  });
  assert.equal(stats.actionSegments, 33);
  assert.equal(stats.ballDetections, 1519);
  assert.equal(stats.shotCount, null);
  assert.equal(stats.contactCount, null);
  assert.equal(stats.shotCountSource, "unavailable");
});

