export type JointMeasurement = { status: "measured" | "not_measured"; unit: string; coverage: number; observation_count: number;
  source_frames: number[]; median?: number; p10?: number; p90?: number; range?: number; min?: number; max?: number;
  reason?: string; angular_speed_p90_deg_s?: number };
export type JointPhase = { phase_id: string; label: string; status: "kinematic_candidate"; start_ms: number; end_ms: number;
  anchor_ms: number; physical_event_verified: false; metrics: Record<string, JointMeasurement>;
  source_frame?: number; source_start_frame?: number; source_end_frame?: number; method?: string;
  window_semantics?: "overlapping_kinematic_observation_not_strict_state_transition"; required_for_physical_event?: string[] };
export type JointKinematics = { version: "body-joint-kinematics-v1"; metrics: Record<string, JointMeasurement>; phases: JointPhase[];
  coordinate_system?: "body_relative_native_3d"; measurement_semantics?: "monocular_model_estimate";
  minimum_joint_coverage?: .7; metric_units?: "degrees_or_body_proportions_not_world_metres" };
const object = (v: unknown): Record<string, unknown> | null => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
function metrics(v: unknown): Record<string, JointMeasurement> | null {
  const items = object(v); if (!items || Object.keys(items).length > 50) return null;
  const result: Record<string, JointMeasurement> = {};
  for (const [key, raw] of Object.entries(items)) {
    const item = object(raw);
    if (!/^[a-z_]+$/.test(key) || !item || !["measured", "not_measured"].includes(String(item.status))
      || !["deg", "shoulder_width"].includes(String(item.unit)) || !finite(item.coverage) || item.coverage < 0 || item.coverage > 1
      || !Number.isSafeInteger(item.observation_count) || Number(item.observation_count) < 0 || !Array.isArray(item.source_frames)
      || item.source_frames.length !== item.observation_count || item.source_frames.some((f, i, a) => !Number.isSafeInteger(f) || f < 0 || i > 0 && f <= a[i - 1])) return null;
    if (item.status === "measured" && (![item.median, item.p10, item.p90, item.range].every(finite) || item.coverage < .7 || item.observation_count === 0 || Number(item.range) < 0
      || Number(item.p10) > Number(item.median) || Number(item.median) > Number(item.p90))) return null;
    if ((item.min !== undefined || item.max !== undefined) && (item.status !== "measured" || !finite(item.min) || !finite(item.max)
      || item.min > Number(item.p10) || item.max < Number(item.p90) || Math.abs(item.max - item.min - Number(item.range)) > .0001)) return null;
    if (item.reason !== undefined && (typeof item.reason !== "string" || item.reason.length > 200 || item.status !== "not_measured")) return null;
    if (item.angular_speed_p90_deg_s !== undefined && (!finite(item.angular_speed_p90_deg_s) || item.angular_speed_p90_deg_s < 0)) return null;
    result[key] = { status: item.status as JointMeasurement["status"], unit: String(item.unit), coverage: item.coverage,
      observation_count: Number(item.observation_count), source_frames: [...item.source_frames],
      ...(item.status === "measured" ? { median: Number(item.median), p10: Number(item.p10), p90: Number(item.p90), range: Number(item.range) } : {}),
      ...(finite(item.min) && finite(item.max) ? { min: item.min, max: item.max } : {}),
      ...(typeof item.reason === "string" ? { reason: item.reason } : {}),
      ...(finite(item.angular_speed_p90_deg_s) ? { angular_speed_p90_deg_s: item.angular_speed_p90_deg_s } : {}) };
  }
  return result;
}
export function validatedJointKinematics(value: unknown, startMs: number, endMs: number): JointKinematics | null {
  const item = object(value), observed = metrics(item?.metrics);
  if (!item || item.version !== "body-joint-kinematics-v1" || !observed || !Array.isArray(item.phases) || item.phases.length !== 10) return null;
  if (item.coordinate_system !== undefined && item.coordinate_system !== "body_relative_native_3d"
    || item.measurement_semantics !== undefined && item.measurement_semantics !== "monocular_model_estimate"
    || item.minimum_joint_coverage !== undefined && item.minimum_joint_coverage !== .7
    || item.metric_units !== undefined && item.metric_units !== "degrees_or_body_proportions_not_world_metres") return null;
  const phases: JointPhase[] = [];
  for (const [i, raw] of item.phases.entries()) {
    const p = object(raw), measured = metrics(p?.metrics);
    if (!p || p.phase_id !== `M${String(i + 1).padStart(2, "0")}` || typeof p.label !== "string" || p.label.length > 100
      || p.status !== "kinematic_candidate" || p.physical_event_verified !== false || !measured
      || !finite(p.start_ms) || !finite(p.end_ms) || !finite(p.anchor_ms) || p.start_ms < startMs || p.end_ms > endMs
      || p.start_ms > p.end_ms || p.anchor_ms < p.start_ms || p.anchor_ms > p.end_ms) return null;
    if (p.source_frame !== undefined && (!Number.isSafeInteger(p.source_frame) || Number(p.source_frame) < 0)
      || p.method !== undefined && (typeof p.method !== "string" || !/^[a-z0-9_]{1,120}$/.test(p.method))
      || p.window_semantics !== undefined && p.window_semantics !== "overlapping_kinematic_observation_not_strict_state_transition"
      || p.required_for_physical_event !== undefined && (!Array.isArray(p.required_for_physical_event)
        || p.required_for_physical_event.length > 10 || p.required_for_physical_event.some(v => typeof v !== "string" || !/^[a-z0-9_]{1,120}$/.test(v)))) return null;
    if (p.source_start_frame !== undefined || p.source_end_frame !== undefined) {
      if (!Number.isSafeInteger(p.source_start_frame) || !Number.isSafeInteger(p.source_end_frame) || !Number.isSafeInteger(p.source_frame)
        || Number(p.source_start_frame) < 0 || Number(p.source_start_frame) > Number(p.source_frame) || Number(p.source_frame) > Number(p.source_end_frame)
        || Object.values(measured).some(m => m.source_frames.some(f => f < Number(p.source_start_frame) || f > Number(p.source_end_frame)))) return null;
    }
    phases.push({ phase_id: p.phase_id, label: p.label, status: p.status, physical_event_verified: false, start_ms: p.start_ms,
      end_ms: p.end_ms, anchor_ms: p.anchor_ms, metrics: measured,
      ...(p.source_frame !== undefined ? { source_frame: Number(p.source_frame) } : {}),
      ...(p.source_start_frame !== undefined ? { source_start_frame: Number(p.source_start_frame), source_end_frame: Number(p.source_end_frame) } : {}),
      ...(typeof p.method === "string" ? { method: p.method } : {}),
      ...(p.window_semantics === "overlapping_kinematic_observation_not_strict_state_transition" ? { window_semantics: p.window_semantics } : {}),
      ...(Array.isArray(p.required_for_physical_event) ? { required_for_physical_event: [...p.required_for_physical_event] as string[] } : {}) });
  }
  return { version: item.version, metrics: observed, phases,
    ...(item.coordinate_system === "body_relative_native_3d" ? { coordinate_system: item.coordinate_system } : {}),
    ...(item.measurement_semantics === "monocular_model_estimate" ? { measurement_semantics: item.measurement_semantics } : {}),
    ...(item.minimum_joint_coverage === .7 ? { minimum_joint_coverage: .7 } : {}),
    ...(item.metric_units === "degrees_or_body_proportions_not_world_metres" ? { metric_units: item.metric_units } : {}) };
}
