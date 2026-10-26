/** A bounded, independently published geometry view; never formal FS03 data. */
export const FOOT_CROSSING_RESPONSE_BYTES = 1048576;
export const FOOT_CROSSING_RESPONSE_VERSION = "foot-crossing-candidates-response-v1";
export const FOOT_CROSSING_ALGORITHM_VERSION = "projected-ankle-order-candidates-v1.0.1";
export const FOOT_CROSSING_ARTIFACT_VERSION = "foot-crossing-candidates-artifact-v1.0.1";
type Pair = [number, number];
type Joint = "left_hip" | "right_hip" | "left_ankle" | "right_ankle";
type ParentAssociation = "mismatch" | "pair_source_bindings_verified" | "weak_source_track_association" | "unverified";
const PARENT_ASSOCIATION_POLICY = "existing_source_conflicts_excluded;legacy_track_only_association_is_weak_not_fully_verified";
export type FootCrossingBinding = {
  frame_index: number; processed_index: number; video_sha256: string; pts: number;
  time_base_num: number; time_base_den: number; source_timestamp_ms: number;
  video_timestamp_ms: number; timestamp_source: "decoder_pts"; primary_player_id: number;
  person_track_id: number; selection_epoch: number; camera_reference_epoch: number;
};
export type FootCrossingIdentity = {
  manifest_sha256: string; artifact_sha256: string; artifact_version: typeof FOOT_CROSSING_ARTIFACT_VERSION;
  algorithm_version: typeof FOOT_CROSSING_ALGORITHM_VERSION; input_bundle_sha256: string;
  source_job_id: string; video_id: string; video_sha256: string; job_summary_sha256: string;
  inputs: Record<"frames" | "events" | "primary", { bytes: number; sha256: string }>;
  source_binding: { reference_version: string; reference_sha256: string; document: { id: "CARD-FS"; sha256: string } };
  producer: { algorithm_version: typeof FOOT_CROSSING_ALGORITHM_VERSION; code: Array<{ name: string; bytes: number; sha256: string }> };
  input_binding_mode: "verified_publication_snapshot";
};
export type FootCrossingCandidate = {
  source_frames: Pair; processed_indexes: Pair; source_bindings: [FootCrossingBinding, FootCrossingBinding];
  source_interval_ms: [{ numerator: number; denominator: number; approx_ms: number }, { numerator: number; denominator: number; approx_ms: number }];
  signed_ankle_order_px: Pair; candidate_quality_status: "unvalidated" | "suspect";
  parent_event_count: number; parent_source_association_counts: Partial<Record<ParentAssociation, number>>;
  parent_source_association_policy: typeof PARENT_ASSOCIATION_POLICY;
  axis: { source: "two_frame_hip_midpoint_displacement_in_camera_reference_image";
    source_frames: Pair; source_pts: Pair; time_base: Pair; fixed_within_pair: true;
    hip_displacement_px: Pair; hip_displacement_length_px: number; numeric_resolution_floor_px: number; available: true; unit_vector: Pair;
    movement_direction_reliability: "unverified"; physical_or_tactical_movement_axis_verified: false };
  local_joint_diagnostics: { source_frames: Pair; participating_joints: ["left_hip", "right_hip", "left_ankle", "right_ankle"];
    processed_to_source: [{ processed_index: number; source_frame_index: number }, { processed_index: number; source_frame_index: number }];
    input_frame_index_domain: "processed_index";
    output_frame_index_domain: "source_frame_index"; assessment_status: "sam_confidence_diagnostics_not_assessed" | "source_track_matched_diagnostics_available" | "not_provided";
    empty_lists_prove_no_anomaly: false; keypoint_jump_candidate_frames_by_joint: Partial<Record<Joint, number[]>>;
    left_right_swap_candidate_joint_pairs: Array<{ left_joint: Joint; right_joint: Joint; frames: number[] }>;
    swap_evidence_truncated: boolean };
  quality: { support_semantics: "model_geometry" | "confidence_qualified_image_points"; rtm_confidence_min: null | 0.35 };
  candidate_only: true; scoring_allowed: false; formal_step_verified: false; technical_grade: null;
};
export type FootCrossingCounts = { frames: number; adjacent_sequence_pairs: number; candidates: number;
  no_exchange: number; excluded: number; candidate_quality: Partial<Record<"unvalidated" | "suspect", number>>; reasons: Record<string, number> };
export type FootCrossingOutputLimits = { candidate_limit: number; exclusion_limit: number;
  retention: "first_in_source_sequence_order"; candidates_truncated: boolean; exclusions_truncated: boolean;
  stored_candidate_count: number; total_candidate_count: number };
type CommonResponse = { schema_version: typeof FOOT_CROSSING_RESPONSE_VERSION; job_id: string; reason_code: string;
  scoring_allowed: false; formal_step_verified: false; technical_grade: null };
export type FootCrossingResponse = CommonResponse & ({ status: "available"; identity: FootCrossingIdentity;
  counts: FootCrossingCounts; output_limits: FootCrossingOutputLimits; candidates: FootCrossingCandidate[] } | {
  status: "not_computed" | "artifact_unavailable" | "binding_conflict" | "unsupported_version" | "invalid_artifact" | "limit_exceeded";
  identity: null; counts: null; output_limits: null; candidates: [] });

const JOINTS = new Set(["left_hip", "right_hip", "left_ankle", "right_ankle"]);
const CODE = ["src/rallymate_features/foot_crossing.py", "src/rallymate_scoring/source_assessment.py",
  "src/rallymate_features/coordinates.py", "src/rallymate_features/schemas.py", "src/rallymate_features/validity.py"];
const UNAVAILABLE = new Set(["not_computed", "artifact_unavailable", "binding_conflict", "unsupported_version", "invalid_artifact", "limit_exceeded"]);
type Obj = Record<string, unknown>;
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error("独立换序线索与当前任务或支持的数据契约不匹配。"); }
function object(value: unknown): Obj { requireValue(value !== null && typeof value === "object" && !Array.isArray(value)); return value as Obj; }
function keys(value: Obj, names: string[]) { requireValue(Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name))); }
function integer(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max; }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER; }
function hash(value: unknown): string { requireValue(typeof value === "string" && /^[a-f\d]{64}$/i.test(value)); return value.toLowerCase(); }
function id(value: unknown): value is string { return typeof value === "string" && /^[A-Za-z\d_-]{1,80}$/.test(value); }
function code(value: unknown): value is string { return typeof value === "string" && /^[a-z][a-z\d_]{0,159}$/.test(value); }
function pair(value: unknown): Pair { requireValue(Array.isArray(value) && value.length === 2 && value.every(finite)); return value as Pair; }
function equalPair(a: unknown, b: Pair) { const parsed = pair(a); requireValue(parsed[0] === b[0] && parsed[1] === b[1]); }
function flags(value: Obj) { requireValue(value.scoring_allowed === false && value.formal_step_verified === false && value.technical_grade === null); }

function boundedTree(raw: unknown) {
  const stack: Array<[unknown, number]> = [[raw, 0]];
  const seen = new Set<object>(); let nodes = 0;
  while (stack.length) {
    const [value, depth] = stack.pop()!;
    requireValue(++nodes <= 120000 && depth <= 24);
    if (value !== null && typeof value === "object") {
      requireValue(!seen.has(value)); seen.add(value);
      if (Array.isArray(value)) { requireValue(value.length <= 128); for (const child of value) stack.push([child, depth + 1]); }
      else { const entries = Object.entries(value); requireValue(entries.length <= 64);
        for (const [name, child] of entries) { requireValue(name.length <= 160); stack.push([child, depth + 1]); } }
    } else requireValue(value === null || typeof value === "boolean" || finite(value) || typeof value === "string" && value.length <= 800);
  }
  requireValue(new TextEncoder().encode(JSON.stringify(raw)).byteLength <= FOOT_CROSSING_RESPONSE_BYTES);
}

function identityOf(raw: unknown): FootCrossingIdentity {
  const value = object(raw);
  keys(value, ["manifest_sha256", "artifact_sha256", "artifact_version", "algorithm_version", "input_bundle_sha256", "source_job_id", "video_id", "video_sha256", "job_summary_sha256", "inputs", "source_binding", "producer", "input_binding_mode"]);
  for (const field of ["manifest_sha256", "artifact_sha256", "input_bundle_sha256", "video_sha256", "job_summary_sha256"]) hash(value[field]);
  requireValue(value.artifact_version === FOOT_CROSSING_ARTIFACT_VERSION && value.algorithm_version === FOOT_CROSSING_ALGORITHM_VERSION
    && value.input_binding_mode === "verified_publication_snapshot" && id(value.source_job_id) && id(value.video_id));
  const inputs = object(value.inputs); keys(inputs, ["frames", "events", "primary"]);
  for (const item of Object.values(inputs)) { const input = object(item); keys(input, ["bytes", "sha256"]); requireValue(integer(input.bytes)); hash(input.sha256); }
  const source = object(value.source_binding), document = object(source.document);
  keys(source, ["reference_version", "reference_sha256", "document"]); keys(document, ["id", "sha256"]);
  // Pinned packaged CARD-FS identity for the supported v1.0.1 publication.
  requireValue(source.reference_version === "scoring-reference-20261004"
    && hash(source.reference_sha256) === "3f58ebdc561912cbe43211678475bcc07db84a6dff8247024d0c1be2ba70a812"
    && document.id === "CARD-FS" && hash(document.sha256) === "8b49a066398c1b59e749a921fad5b943e49c6f1f16b82e5df5d6d996ddbfe41e");
  const producer = object(value.producer); keys(producer, ["algorithm_version", "code"]);
  requireValue(producer.algorithm_version === FOOT_CROSSING_ALGORITHM_VERSION && Array.isArray(producer.code) && producer.code.length >= 5 && producer.code.length <= 32);
  const names = new Set<string>();
  for (const item of producer.code) { const entry = object(item); keys(entry, ["name", "bytes", "sha256"]);
    requireValue(typeof entry.name === "string" && /^[A-Za-z\d_./-]+$/.test(entry.name) && !entry.name.startsWith("/")
      && !entry.name.split("/").some(part => part === ".." || part === "." || part === "") && !names.has(entry.name) && integer(entry.bytes, 1));
    hash(entry.sha256); names.add(entry.name); }
  requireValue(CODE.every(name => names.has(name)));
  return value as FootCrossingIdentity;
}

function candidateOf(raw: unknown, identity: FootCrossingIdentity): FootCrossingCandidate {
  const row = object(raw);
  keys(row, ["source_frames", "processed_indexes", "source_bindings", "source_interval_ms", "signed_ankle_order_px", "candidate_quality_status", "parent_event_count", "parent_source_association_counts", "parent_source_association_policy", "axis", "local_joint_diagnostics", "quality", "candidate_only", "scoring_allowed", "formal_step_verified", "technical_grade"]);
  flags(row); requireValue(row.candidate_only === true);
  const associations = object(row.parent_source_association_counts);
  requireValue(integer(row.parent_event_count) && row.parent_source_association_policy === PARENT_ASSOCIATION_POLICY
    && Object.entries(associations).every(([name, count]) => ["mismatch", "pair_source_bindings_verified", "weak_source_track_association", "unverified"].includes(name) && integer(count))
    && Object.values(associations).reduce<number>((sum, count) => sum + (count as number), 0) === row.parent_event_count);
  const frames = pair(row.source_frames), processed = pair(row.processed_indexes), signed = pair(row.signed_ankle_order_px);
  requireValue(frames.every(value => integer(value)) && frames[1] === frames[0] + 1 && processed.every(value => integer(value)) && processed[1] === processed[0] + 1);
  requireValue(signed[0] < 0 && signed[1] > 0 || signed[0] > 0 && signed[1] < 0);
  requireValue(Array.isArray(row.source_bindings) && row.source_bindings.length === 2 && Array.isArray(row.source_interval_ms) && row.source_interval_ms.length === 2);
  const bindings = row.source_bindings.map(object), exact: Array<[bigint, bigint]> = [];
  for (let index = 0; index < 2; index++) {
    const binding = bindings[index], rational = object(row.source_interval_ms[index]);
    keys(binding, ["frame_index", "processed_index", "video_sha256", "pts", "time_base_num", "time_base_den", "source_timestamp_ms", "video_timestamp_ms", "timestamp_source", "primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch"]);
    for (const name of ["frame_index", "processed_index", "video_timestamp_ms", "primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch"]) requireValue(integer(binding[name]));
    requireValue(integer(binding.pts, -Number.MAX_SAFE_INTEGER));
    requireValue(binding.frame_index === frames[index] && binding.processed_index === processed[index] && binding.timestamp_source === "decoder_pts"
      && hash(binding.video_sha256) === hash(identity.video_sha256) && integer(binding.time_base_num, 1) && integer(binding.time_base_den, 1));
    const numerator = BigInt(binding.pts as number) * BigInt(binding.time_base_num) * BigInt(1000), denominator = BigInt(binding.time_base_den);
    keys(rational, ["numerator", "denominator", "approx_ms"]);
    requireValue(integer(rational.numerator, -Number.MAX_SAFE_INTEGER) && integer(rational.denominator, 1)
      && BigInt(rational.numerator) * denominator === numerator * BigInt(rational.denominator));
    const stamp = Number(numerator) / Number(denominator);
    requireValue(finite(rational.approx_ms) && Math.abs(rational.approx_ms - stamp) < 1e-6
      && finite(binding.source_timestamp_ms) && Math.abs(binding.source_timestamp_ms - stamp) <= .51);
    exact.push([numerator, denominator]);
  }
  const [a, b] = bindings, deltaNumerator = exact[1][0] * exact[0][1] - exact[0][0] * exact[1][1], deltaDenominator = exact[0][1] * exact[1][1];
  requireValue(deltaNumerator > BigInt(0) && deltaNumerator <= BigInt(160) * deltaDenominator);
  const videoDelta = (b.video_timestamp_ms as number) - (a.video_timestamp_ms as number);
  requireValue(videoDelta > 0 && Math.abs(Number(deltaNumerator) / Number(deltaDenominator) - videoDelta) <= 1.02);
  // Logical primary IDs and native source-track IDs are distinct domains.
  for (const key of ["primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch", "time_base_num", "time_base_den"]) requireValue(a[key] === b[key]);
  const axis = object(row.axis);
  keys(axis, ["source", "source_frames", "source_pts", "time_base", "fixed_within_pair", "hip_displacement_px", "hip_displacement_length_px", "numeric_resolution_floor_px", "available", "unit_vector", "movement_direction_reliability", "physical_or_tactical_movement_axis_verified"]);
  requireValue(axis.source === "two_frame_hip_midpoint_displacement_in_camera_reference_image" && axis.fixed_within_pair === true && axis.available === true
    && axis.movement_direction_reliability === "unverified" && axis.physical_or_tactical_movement_axis_verified === false);
  equalPair(axis.source_frames, frames); equalPair(axis.source_pts, [a.pts as number, b.pts as number]); equalPair(axis.time_base, [a.time_base_num as number, a.time_base_den as number]);
  const unit = pair(axis.unit_vector), displacement = pair(axis.hip_displacement_px), length = axis.hip_displacement_length_px;
  requireValue(finite(length) && length > 0 && finite(axis.numeric_resolution_floor_px) && axis.numeric_resolution_floor_px >= 0 && axis.numeric_resolution_floor_px < length
    && Math.abs(Math.hypot(...unit) - 1) <= 1e-6
    && Math.abs(Math.hypot(...displacement) - length) <= 1e-6 * Math.max(1, length)
    && unit.every((value, index) => Math.abs(value - displacement[index] / length) <= 1e-6));
  const quality = object(row.quality); keys(quality, ["support_semantics", "rtm_confidence_min"]);
  const model = quality.support_semantics === "model_geometry";
  requireValue(model ? quality.rtm_confidence_min === null : quality.support_semantics === "confidence_qualified_image_points" && quality.rtm_confidence_min === .35);
  const diagnostics = object(row.local_joint_diagnostics);
  keys(diagnostics, ["source_frames", "participating_joints", "processed_to_source", "input_frame_index_domain", "output_frame_index_domain", "assessment_status", "empty_lists_prove_no_anomaly", "keypoint_jump_candidate_frames_by_joint", "left_right_swap_candidate_joint_pairs", "swap_evidence_truncated"]);
  equalPair(diagnostics.source_frames, frames);
  requireValue(Array.isArray(diagnostics.participating_joints) && diagnostics.participating_joints.length === JOINTS.size
    && [...JOINTS].every((name, index) => (diagnostics.participating_joints as unknown[])[index] === name)
    && Array.isArray(diagnostics.processed_to_source) && diagnostics.processed_to_source.length === 2);
  diagnostics.processed_to_source.forEach((value, index) => { const mapping = object(value); keys(mapping, ["processed_index", "source_frame_index"]);
    requireValue(mapping.processed_index === processed[index] && mapping.source_frame_index === frames[index]); });
  requireValue(diagnostics.input_frame_index_domain === "processed_index" && diagnostics.output_frame_index_domain === "source_frame_index"
    && diagnostics.empty_lists_prove_no_anomaly === false && typeof diagnostics.swap_evidence_truncated === "boolean"
    && (model ? diagnostics.assessment_status === "sam_confidence_diagnostics_not_assessed" : ["source_track_matched_diagnostics_available", "not_provided"].includes(String(diagnostics.assessment_status))));
  const localFrames = (value: unknown): number[] => { requireValue(Array.isArray(value) && value.length > 0 && value.length <= 2
    && value.every(frame => integer(frame) && frames.includes(frame)) && new Set(value).size === value.length); return value; };
  const jumps = object(diagnostics.keypoint_jump_candidate_frames_by_joint);
  for (const [name, values] of Object.entries(jumps)) { requireValue(JOINTS.has(name)); localFrames(values); }
  requireValue(Array.isArray(diagnostics.left_right_swap_candidate_joint_pairs) && diagnostics.left_right_swap_candidate_joint_pairs.length <= 8);
  for (const item of diagnostics.left_right_swap_candidate_joint_pairs) { const swap = object(item); keys(swap, ["left_joint", "right_joint", "frames"]);
    requireValue(JOINTS.has(String(swap.left_joint)) && JOINTS.has(String(swap.right_joint))); localFrames(swap.frames); }
  const suspect = Object.keys(jumps).length > 0 || diagnostics.left_right_swap_candidate_joint_pairs.length > 0;
  requireValue(row.candidate_quality_status === (suspect ? "suspect" : "unvalidated"));
  return row as FootCrossingCandidate;
}

/** Structural/cross-field validation of the server's snapshot, not browser rehashing of source files. */
export function parseFootCrossingResponse(raw: unknown, expectedJobId: string): FootCrossingResponse {
  boundedTree(raw); const response = object(raw);
  keys(response, ["schema_version", "job_id", "status", "reason_code", "identity", "counts", "output_limits", "candidates", "scoring_allowed", "formal_step_verified", "technical_grade"]);
  requireValue(id(expectedJobId) && response.job_id === expectedJobId && response.schema_version === FOOT_CROSSING_RESPONSE_VERSION && code(response.reason_code));
  flags(response); requireValue(Array.isArray(response.candidates));
  if (response.status !== "available") {
    requireValue(UNAVAILABLE.has(String(response.status)) && response.identity === null && response.counts === null && response.output_limits === null
      && response.candidates.length === 0 && !["available_candidates", "available_zero_candidates"].includes(response.reason_code));
  } else {
    const identity = identityOf(response.identity), counts = object(response.counts), limits = object(response.output_limits);
    keys(counts, ["frames", "adjacent_sequence_pairs", "candidates", "no_exchange", "excluded", "candidate_quality", "reasons"]);
    for (const key of ["frames", "adjacent_sequence_pairs", "candidates", "no_exchange", "excluded"]) requireValue(integer(counts[key]));
    requireValue(counts.adjacent_sequence_pairs === Math.max(0, (counts.frames as number) - 1)
      && counts.adjacent_sequence_pairs === (counts.candidates as number) + (counts.no_exchange as number) + (counts.excluded as number));
    for (const key of ["candidate_quality", "reasons"]) { const breakdown = object(counts[key]);
      requireValue(Object.keys(breakdown).length <= 64 && Object.entries(breakdown).every(([name, count]) => code(name) && integer(count)
        && (key !== "candidate_quality" || ["unvalidated", "suspect"].includes(name))));
      requireValue(Object.values(breakdown).reduce<number>((sum, value) => sum + (value as number), 0) === counts[key === "candidate_quality" ? "candidates" : "adjacent_sequence_pairs"]); }
    const reasons = object(counts.reasons);
    requireValue((reasons.strict_projected_order_exchange ?? 0) === counts.candidates && (reasons.same_projected_order ?? 0) === counts.no_exchange);
    keys(limits, ["candidate_limit", "exclusion_limit", "retention", "candidates_truncated", "exclusions_truncated", "stored_candidate_count", "total_candidate_count"]);
    requireValue(integer(limits.candidate_limit, 0, 128) && integer(limits.exclusion_limit, 0, 32) && limits.retention === "first_in_source_sequence_order"
      && response.candidates.length === Math.min(counts.candidates as number, limits.candidate_limit)
      && limits.stored_candidate_count === response.candidates.length && limits.total_candidate_count === counts.candidates
      && limits.candidates_truncated === ((counts.candidates as number) > response.candidates.length)
      && limits.exclusions_truncated === ((counts.excluded as number) > limits.exclusion_limit)
      && response.reason_code === (counts.candidates === 0 ? "available_zero_candidates" : "available_candidates"));
    const candidates = response.candidates.map(row => candidateOf(row, identity));
    requireValue(candidates.every((row, index) => index === 0 || row.source_frames[0] > candidates[index - 1].source_frames[0]));
    for (const quality of ["unvalidated", "suspect"]) requireValue(candidates.filter(row => row.candidate_quality_status === quality).length <= Number(object(counts.candidate_quality)[quality] ?? 0));
  }
  // Copy only after full validation; UI selection must never mutate a caller's report/fixture.
  return JSON.parse(JSON.stringify(response)) as FootCrossingResponse;
}

export function footCrossingIdentityKey(identity: FootCrossingIdentity): string {
  return JSON.stringify([identity.manifest_sha256.toLowerCase(), identity.artifact_sha256.toLowerCase(), identity.artifact_version,
    identity.algorithm_version, identity.input_bundle_sha256.toLowerCase(), identity.source_job_id, identity.video_id,
    identity.video_sha256.toLowerCase(), identity.job_summary_sha256.toLowerCase()]);
}
