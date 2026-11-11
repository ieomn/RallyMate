import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
const source = fs.readFileSync(new URL("../app/lib/modality-assessment.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { modalityAssessmentOf } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const sha = "a".repeat(64), episodes = [{ episode_id: "action-1", start_ms: 0, end_ms: 1000 }];
function report() {
  return { version: "observed-modalities-v1.0.0", source_video_sha256: sha, frame_count: 30,
    summary: { ball_observed_frames: 10, racket_observed_frames: 20, court_calibrated_frames: 0, confirmed_landing_count: 0 },
    per_episode: [{ ...episodes[0], ball: { total_frame_count: 30, observed_frame_count: 10, physical_speed_m_s: null, spin_rpm: null,
      speed_unit: "frame_long_edge_per_second", tracks: [{ samples: [{ speed: 1.2, source_frames: [1, 2] }] }] },
      racket: { observed_frame_count: 20 }, court: { calibrated_frame_count: 0 } }],
    tactics: {}, limitations: ["Image speed is not physical speed"], missing_modality_disables_pose_score: false };
}
test("actual image measurements retain source samples without inventing physical velocity", () => {
  const data = report(); assert.deepEqual(modalityAssessmentOf(data, sha, episodes), data);
});
test("wrong source, episode, nonfinite motion, or forged physical unit is rejected", () => {
  for (const mutate of [r => { r.source_video_sha256 = "b".repeat(64); }, r => { r.per_episode[0].episode_id = "other"; },
    r => { r.per_episode[0].ball.tracks[0].samples[0].speed = Infinity; }, r => { r.per_episode[0].ball.physical_speed_m_s = 30; }]) {
    const data = report(); mutate(data); assert.equal(modalityAssessmentOf(data, sha, episodes), null);
  }
});
