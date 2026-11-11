import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/joint-kinematics.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { validatedJointKinematics } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
function fixture() {
  const metric = { status: "measured", unit: "deg", coverage: 1, observation_count: 3, source_frames: [0, 1, 2],
    min: 10, p10: 12, median: 20, p90: 28, max: 30, range: 20, angular_speed_p90_deg_s: 100 };
  return { version: "body-joint-kinematics-v1", coordinate_system: "body_relative_native_3d",
    measurement_semantics: "monocular_model_estimate", minimum_joint_coverage: .7,
    metric_units: "degrees_or_body_proportions_not_world_metres", metrics: { left_knee_flexion_deg: metric },
    phases: Array.from({ length: 10 }, (_, i) => ({ phase_id: `M${String(i + 1).padStart(2, "0")}`, label: "Motion",
      status: "kinematic_candidate", start_ms: 0, anchor_ms: 100, end_ms: 200, source_frame: 1, source_start_frame: 0, source_end_frame: 2,
      method: "observed_joint_angle_peak", physical_event_verified: false,
      window_semantics: "overlapping_kinematic_observation_not_strict_state_transition",
      required_for_physical_event: i === 7 ? ["ball_racket_contact_for_physical_impact"] : [],
      metrics: { left_knee_flexion_deg: { ...metric } } })) };
}

test("joint evidence preserves physical semantics, exact source anchors, and ROM extrema", () => {
  const raw = fixture();
  assert.deepEqual(validatedJointKinematics(raw, 0, 300), raw);
});
test("joint evidence rejects wrong coordinate claims, source frames and inconsistent ROM", () => {
  for (const mutate of [r => { r.coordinate_system = "calibrated_world_metres"; },
    r => { r.measurement_semantics = "measured_ground_truth"; },
    r => { r.minimum_joint_coverage = .2; },
    r => { r.phases[0].source_frame = -1; },
    r => { r.phases[0].source_start_frame = 1; },
    r => { r.phases[0].physical_event_verified = true; },
    r => { r.phases[0].method = {}; },
    r => { r.metrics.left_knee_flexion_deg.max = 900; },
    r => { r.metrics.left_knee_flexion_deg.observation_count = 0; }]) {
    const raw = fixture(); mutate(raw);
    assert.equal(validatedJointKinematics(raw, 0, 300), null);
  }
});
test("missing joint observations retain an explicit reason without becoming zero ROM", () => {
  const raw = fixture();
  raw.metrics.left_knee_flexion_deg = { status: "not_measured", unit: "deg", coverage: .3,
    observation_count: 1, source_frames: [1], reason: "insufficient_joint_observations" };
  assert.deepEqual(validatedJointKinematics(raw, 0, 300), raw);
});
