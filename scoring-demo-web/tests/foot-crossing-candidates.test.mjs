import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const cache = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (cache.has(file.href)) return cache.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const result = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, result); return result;
}
const { parseFootCrossingResponse: parse, footCrossingIdentityKey: identityKey, FOOT_CROSSING_RESPONSE_BYTES: LIMIT } = await import(compiledUrl(new URL("../app/lib/foot-crossing-candidates.ts", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const jobId = "synthetic-crossing-job";
const configured = { baseUrl: "https://gateway.example", jobsPath: "/v1/jobs" };

// Explicitly synthetic parser/transport fixture, NOT real97s evidence or a
// published source snapshot. Pair arithmetic follows the frozen two-frame
// fixture: source 170/171, primary 1 vs native track 8; video origin is 5000 ms.
// The CARD-FS reference pins are copied from the packaged source catalogue.
// Other hashes/bytes are placeholders; server publication hash validation is
// tested by the backend owner, never asserted by this browser-only fixture.
function fixture() {
  const source = [170, 171], processed = [20, 21];
  const candidate = {
    source_frames: source, processed_indexes: processed,
    source_bindings: source.map((frame, i) => ({ frame_index: frame, processed_index: processed[i], video_sha256: "a".repeat(64), pts: 3400 + i * 20,
      time_base_num: 1, time_base_den: 600, source_timestamp_ms: i ? 5700 : 5667, video_timestamp_ms: i ? 700 : 667,
      timestamp_source: "decoder_pts", primary_player_id: 1, person_track_id: 8, selection_epoch: 0, camera_reference_epoch: 0 })),
    source_interval_ms: [{ numerator: 17000, denominator: 3, approx_ms: 17000 / 3 }, { numerator: 5700, denominator: 1, approx_ms: 5700 }],
    signed_ankle_order_px: [-40, 10], candidate_quality_status: "unvalidated",
    parent_event_count: 0, parent_source_association_counts: {},
    parent_source_association_policy: "existing_source_conflicts_excluded;legacy_track_only_association_is_weak_not_fully_verified",
    axis: { source: "two_frame_hip_midpoint_displacement_in_camera_reference_image", source_frames: [170, 171], source_pts: [3400, 3420], time_base: [1, 600],
      fixed_within_pair: true, hip_displacement_px: [10, 0], hip_displacement_length_px: 10, numeric_resolution_floor_px: 1e-12, available: true, unit_vector: [1, 0],
      movement_direction_reliability: "unverified", physical_or_tactical_movement_axis_verified: false },
    local_joint_diagnostics: { source_frames: [170, 171], input_frame_index_domain: "processed_index", output_frame_index_domain: "source_frame_index",
      participating_joints: ["left_hip", "right_hip", "left_ankle", "right_ankle"],
      processed_to_source: [{ processed_index: 20, source_frame_index: 170 }, { processed_index: 21, source_frame_index: 171 }],
      assessment_status: "not_provided", empty_lists_prove_no_anomaly: false, keypoint_jump_candidate_frames_by_joint: {}, left_right_swap_candidate_joint_pairs: [], swap_evidence_truncated: false },
    quality: { support_semantics: "confidence_qualified_image_points", rtm_confidence_min: .35 }, candidate_only: true, scoring_allowed: false, formal_step_verified: false, technical_grade: null,
  };
  return { schema_version: "foot-crossing-candidates-response-v1", job_id: jobId, status: "available", reason_code: "available_candidates",
    identity: { manifest_sha256: "1".repeat(64), artifact_sha256: "2".repeat(64), artifact_version: "foot-crossing-candidates-artifact-v1.0.1",
      algorithm_version: "projected-ankle-order-candidates-v1.0.1", input_bundle_sha256: "3".repeat(64), source_job_id: "synthetic-source-job", video_id: "synthetic-native-video",
      video_sha256: "a".repeat(64), job_summary_sha256: "4".repeat(64), inputs: { frames: { bytes: 100, sha256: "5".repeat(64) }, events: { bytes: 100, sha256: "6".repeat(64) }, primary: { bytes: 100, sha256: "7".repeat(64) } },
      source_binding: { reference_version: "scoring-reference-20261004", reference_sha256: "3f58ebdc561912cbe43211678475bcc07db84a6dff8247024d0c1be2ba70a812", document: { id: "CARD-FS", sha256: "8b49a066398c1b59e749a921fad5b943e49c6f1f16b82e5df5d6d996ddbfe41e" } },
      producer: { algorithm_version: "projected-ankle-order-candidates-v1.0.1", code: ["src/rallymate_features/foot_crossing.py", "src/rallymate_scoring/source_assessment.py", "src/rallymate_features/coordinates.py", "src/rallymate_features/schemas.py", "src/rallymate_features/validity.py"].map(name => ({ name, bytes: 1, sha256: "8".repeat(64) })) },
      input_binding_mode: "verified_publication_snapshot" },
    counts: { frames: 2, adjacent_sequence_pairs: 1, candidates: 1, no_exchange: 0, excluded: 0, candidate_quality: { unvalidated: 1 }, reasons: { strict_projected_order_exchange: 1 } },
    output_limits: { candidate_limit: 64, exclusion_limit: 16, retention: "first_in_source_sequence_order", candidates_truncated: false, exclusions_truncated: false, stored_candidate_count: 1, total_candidate_count: 1 },
    candidates: [candidate], scoring_allowed: false, formal_step_verified: false, technical_grade: null };
}
function unavailable(status = "not_computed") {
  return { schema_version: "foot-crossing-candidates-response-v1", job_id: jobId, status, reason_code: "publication_not_found", identity: null,
    counts: null, output_limits: null, candidates: [], scoring_allowed: false, formal_step_verified: false, technical_grade: null };
}

test("synthetic source-bound pair retains two clock domains, distinct primary/source IDs, exact signs and copy isolation", () => {
  const raw = fixture(), original = structuredClone(raw), parsed = parse(raw, jobId);
  assert.deepEqual(parsed, raw);
  assert.deepEqual(parsed.candidates[0].signed_ankle_order_px, [-40, 10]);
  assert.equal(parsed.candidates[0].source_bindings[0].primary_player_id, 1);
  assert.equal(parsed.candidates[0].source_bindings[0].person_track_id, 8);
  assert.equal(parsed.candidates[0].source_interval_ms[0].numerator, 17000);
  assert.equal(parsed.candidates[0].source_bindings[0].video_timestamp_ms, 667);
  parsed.candidates[0].signed_ankle_order_px[0] = 99; assert.deepEqual(raw, original);
  const key = identityKey(raw.identity);
  for (const field of ["manifest_sha256", "artifact_sha256", "input_bundle_sha256", "job_summary_sha256"]) {
    const changed = structuredClone(raw.identity); changed[field] = "f".repeat(64); assert.notEqual(identityKey(changed), key);
  }
});

test("actual saved RTMPose pair passes the pure backend projection and browser schema without promoting weak source association", () => {
  // Only the candidate is real saved evidence. Identity/counts/limits are a
  // synthetic one-pair schema wrapper, not publication hashes or full97 counts.
  const saved = JSON.parse(fs.readFileSync(new URL("./fixtures/foot-crossing-real-pair-projection.json", import.meta.url), "utf8"));
  assert.equal(saved.provenance.source.sha256, "b49ff2d3dfaff3bd71cc08793b50c69125aa9cc6b208e546da0b165d1bed99b6");
  assert.equal(saved.provenance.source_row_sha256, "5e6b71dedd4c23f9076c1e6d6efce9a280d4c3ab06bba0a2532033de73fc2ebd");
  assert.deepEqual(saved.provenance.source_frames, [170, 171]);
  assert.deepEqual(saved.provenance.source_pts, [3400, 3420]);
  assert.deepEqual(saved.provenance.source_timebase, [[1, 600], [1, 600]]);
  const raw = fixture(); raw.identity.video_sha256 = saved.candidate.source_bindings[0].video_sha256;
  raw.candidates = [structuredClone(saved.candidate)]; raw.counts.candidate_quality = { suspect: 1 };
  const parsed = parse(raw, jobId), candidate = parsed.candidates[0];
  assert.deepEqual(candidate, saved.candidate);
  assert.deepEqual(candidate.parent_source_association_counts, { weak_source_track_association: 1 });
  assert.equal(candidate.candidate_quality_status, "suspect");
  assert.deepEqual(candidate.local_joint_diagnostics.keypoint_jump_candidate_frames_by_joint, { right_ankle: [171] });
  assert.deepEqual(candidate.local_joint_diagnostics.left_right_swap_candidate_joint_pairs, []);
  assert.deepEqual(candidate.processed_indexes, [170, 171]);
  assert.deepEqual(parsed.counts, raw.counts); assert.deepEqual(parsed.output_limits, raw.output_limits);
  assert.deepEqual(parsed.identity.source_binding, raw.identity.source_binding);
  // Synthetic negative native-clock origin, allowed by the reader contract.
  // This mutation is not part of the saved RTMPose evidence above.
  const negativeOrigin = structuredClone(raw), shifted = negativeOrigin.candidates[0];
  shifted.source_bindings.forEach(binding => { binding.pts -= 6000; binding.source_timestamp_ms -= 10000; });
  shifted.source_interval_ms.forEach(stamp => { stamp.numerator -= 10000 * stamp.denominator; stamp.approx_ms -= 10000; });
  shifted.axis.source_pts = shifted.source_bindings.map(binding => binding.pts);
  assert.deepEqual(parse(negativeOrigin, jobId).candidates[0].source_interval_ms, shifted.source_interval_ms);
  shifted.source_bindings[0].video_timestamp_ms = -1; assert.throws(() => parse(negativeOrigin, jobId));
  const changes = [x => { x.parent_event_count++; }, x => { x.parent_source_association_counts = { fully_verified: 1 }; },
    x => { x.parent_source_association_policy = "track_only_fully_verified"; }, x => { x.axis.numeric_resolution_floor_px = -1; },
    x => { x.axis.numeric_resolution_floor_px = x.axis.hip_displacement_length_px; },
    x => { x.local_joint_diagnostics.participating_joints[0] = "left_elbow"; },
    x => { x.local_joint_diagnostics.processed_to_source[0].processed_index = 20; },
    x => { x.local_joint_diagnostics.processed_to_source[1].source_frame_index = 172; },
    x => { x.local_joint_diagnostics.left_right_swap_candidate_joint_pairs = [{ left_joint: "left_elbow", right_joint: "right_elbow", frames: [171] }]; },
    x => { x.candidate_quality_status = "unvalidated"; }];
  for (const change of changes) { const wrong = structuredClone(raw); change(wrong.candidates[0]); assert.throws(() => parse(wrong, jobId)); }
});

test("unavailable states stay distinct with no candidate counts or identity, never masquerading as verified zero", () => {
  for (const state of ["not_computed", "artifact_unavailable", "binding_conflict", "unsupported_version", "invalid_artifact", "limit_exceeded"]) {
    const raw = unavailable(state); assert.equal(parse(raw, jobId).status, state);
    for (const change of [x => { x.counts = fixture().counts; }, x => { x.identity = fixture().identity; }, x => { x.candidates = fixture().candidates; }, x => { x.reason_code = "available_zero_candidates"; }]) {
      const wrong = structuredClone(raw); change(wrong); assert.throws(() => parse(wrong, jobId));
    }
  }
  assert.throws(() => parse({ ...unavailable(), status: "checking" }, jobId));
});

test("job, supported versions, source catalogue and every formal promotion flag fail closed", () => {
  const changes = [x => { x.job_id = "other"; }, x => { x.schema_version = "v2"; }, x => { x.scoring_allowed = true; }, x => { x.formal_step_verified = true; }, x => { x.technical_grade = "A"; },
    x => { x.identity.algorithm_version = "v9"; }, x => { x.identity.artifact_version = "v9"; }, x => { x.identity.producer.algorithm_version = "v9"; },
    x => { x.identity.input_binding_mode = "mtime_only"; }, x => { x.identity.source_binding.reference_sha256 = "b".repeat(64); }, x => { x.identity.source_binding.document.id = "CARD-GS"; },
    x => { x.identity.producer.code[0].name = "../escape.py"; }, x => { x.identity.producer.code.pop(); },
    ...["scoring_allowed", "formal_step_verified"].map(key => x => { x.candidates[0][key] = true; }),
    x => { x.candidates[0].candidate_only = false; }, x => { x.candidates[0].technical_grade = "A"; }, x => { x.candidates[0].crossing_time_ms = 5680; }, x => { x.candidates[0].candidate_quality_status = "unverified"; }];
  for (const change of changes) { const raw = fixture(); change(raw); assert.throws(() => parse(raw, jobId)); }
});

test("pair provenance rejects original/processed/timebase/PTS/identity and axis inconsistencies without treating x as the axis", () => {
  const changes = [x => { x.source_frames[1]++; }, x => { x.processed_indexes[1]++; }, x => { x.source_bindings[0].video_sha256 = "b".repeat(64); },
    ...["frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch"].map(key => x => { x.source_bindings[1][key]++; }),
    x => { x.source_bindings[0].source_timestamp_ms = 667; }, x => { x.source_bindings[1].video_timestamp_ms = 667; }, x => { x.source_bindings[1].video_timestamp_ms += 10; },
    x => { x.source_interval_ms[0].numerator++; }, x => { x.source_interval_ms[0].approx_ms++; }, x => { x.signed_ankle_order_px[0] = 0; }, x => { x.signed_ankle_order_px[1] = -1; },
    x => { x.axis.source_frames[0]--; }, x => { x.axis.source_pts[0]++; }, x => { x.axis.time_base[1]++; }, x => { x.axis.fixed_within_pair = false; }, x => { x.axis.unit_vector = [0, 1]; },
    x => { x.axis.hip_displacement_length_px = 0; }, x => { x.axis.movement_direction_reliability = "reliable"; }];
  for (const change of changes) { const raw = fixture(); change(raw.candidates[0]); assert.throws(() => parse(raw, jobId)); }
});

test("SAM unassessed confidence and endpoint joint risk preserve meaning while out-of-pair or promoted quality is rejected", () => {
  const sam = fixture(); sam.candidates[0].quality = { support_semantics: "model_geometry", rtm_confidence_min: null };
  sam.candidates[0].local_joint_diagnostics.assessment_status = "sam_confidence_diagnostics_not_assessed";
  assert.equal(parse(sam, jobId).candidates[0].quality.rtm_confidence_min, null);
  const rtm = fixture(), row = rtm.candidates[0]; row.candidate_quality_status = "suspect";
  row.local_joint_diagnostics.assessment_status = "source_track_matched_diagnostics_available";
  row.local_joint_diagnostics.keypoint_jump_candidate_frames_by_joint = { right_ankle: [171] };
  rtm.counts.candidate_quality = { suspect: 1 };
  assert.deepEqual(parse(rtm, jobId).candidates[0].local_joint_diagnostics.keypoint_jump_candidate_frames_by_joint, { right_ankle: [171] });
  for (const change of [x => { x.quality.rtm_confidence_min = .25; }, x => { x.local_joint_diagnostics.empty_lists_prove_no_anomaly = true; },
    x => { x.local_joint_diagnostics.keypoint_jump_candidate_frames_by_joint = { right_ankle: [173] }; }, x => { x.local_joint_diagnostics.keypoint_jump_candidate_frames_by_joint = { left_elbow: [171] }; },
    x => { x.candidate_quality_status = "unvalidated"; }, x => { x.local_joint_diagnostics.source_frames[0]--; }]) {
    const wrong = structuredClone(rtm); change(wrong.candidates[0]); assert.throws(() => parse(wrong, jobId));
  }
});

test("verified zero, bounded truncation and intentionally stored-zero are distinct; contradictory counts never pass", () => {
  const zero = fixture(); zero.reason_code = "available_zero_candidates"; zero.candidates = [];
  Object.assign(zero.counts, { candidates: 0, no_exchange: 1, candidate_quality: {}, reasons: { same_projected_order: 1 } });
  Object.assign(zero.output_limits, { stored_candidate_count: 0, total_candidate_count: 0 });
  assert.equal(parse(zero, jobId).counts.candidates, 0);
  const truncated = fixture(); Object.assign(truncated.counts, { frames: 4, adjacent_sequence_pairs: 3, candidates: 3, candidate_quality: { unvalidated: 3 }, reasons: { strict_projected_order_exchange: 3 } });
  Object.assign(truncated.output_limits, { candidate_limit: 1, total_candidate_count: 3, candidates_truncated: true });
  assert.equal(parse(truncated, jobId).output_limits.total_candidate_count, 3);
  truncated.candidates = []; Object.assign(truncated.output_limits, { candidate_limit: 0, stored_candidate_count: 0 });
  assert.equal(parse(truncated, jobId).counts.candidates, 3);
  for (const change of [x => { x.counts.candidates = 0; }, x => { x.counts.candidate_quality = {}; }, x => { x.output_limits.candidates_truncated = false; }, x => { x.output_limits.total_candidate_count++; }, x => { x.output_limits.exclusion_limit = 33; }]) {
    const wrong = structuredClone(truncated); change(wrong); assert.throws(() => parse(wrong, jobId));
  }
});

test("response tree, strings, counts and candidates are bounded before accepting any display data", () => {
  for (const change of [x => { x.reason_code = "x".repeat(801); }, x => { x.candidates = Array.from({ length: 129 }, () => structuredClone(x.candidates[0])); },
    x => { x.candidates[0].signed_ankle_order_px[0] = Infinity; }, x => { x.identity.inputs.frames.bytes = Number.MAX_SAFE_INTEGER + 1; },
    x => { x.extra = x; }, x => { x.extra = {}; let cursor = x.extra; for (let i = 0; i < 25; i++) cursor = cursor.next = {}; }]) {
    const raw = fixture(); change(raw); assert.throws(() => parse(raw, jobId));
  }
});

test("candidate API performs one ordinary gateway GET and returns parsed data without report negotiation", async () => {
  const requests = [], client = createApiClient(configured, async (url, init) => { requests.push({ url, init }); return Response.json(fixture()); });
  const caller = new AbortController(); const result = await client.getFootCrossingCandidates(jobId, { signal: caller.signal });
  assert.equal(result.status, "available"); assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `https://gateway.example/v1/jobs/${jobId}/foot-crossing-candidates`);
  assert.equal(requests[0].init.method, "GET"); assert.equal(requests[0].init.cache, "no-store");
  assert.ok(requests[0].init.signal instanceof AbortSignal); assert.ok(requests[0].init.headers["X-Request-ID"]);
  for (const status of [201, 202]) {
    let calls = 0; const invalid = createApiClient(configured, async () => { calls++; return Response.json(fixture(), { status }); });
    await assert.rejects(invalid.getFootCrossingCandidates(jobId), error => error.status === 422); assert.equal(calls, 1);
  }
  await assert.rejects(createApiClient(configured, async () => Response.json({ ...fixture(), job_id: "other" })).getFootCrossingCandidates(jobId), error => error.status === 422);
  await assert.rejects(createApiClient(configured, async () => Response.json({ detail: "Missing job" }, { status: 404 })).getFootCrossingCandidates(jobId), error => error.status === 404);
});

test("candidate reader rejects oversized content length and streaming bodies even if the declared length lies", async () => {
  for (const declared of [String(LIMIT + 1), "1", null]) {
    let cancelled = 0, pulls = 0;
    const response = new Response(new ReadableStream({ pull(controller) { pulls++; controller.enqueue(new Uint8Array(LIMIT + 1)); }, cancel() { cancelled++; } }), { headers: declared === null ? {} : { "content-length": declared } });
    await assert.rejects(createApiClient(configured, async () => response).getFootCrossingCandidates(jobId), error => error.status === 422);
    assert.equal(cancelled, 1); assert.ok(pulls <= 2);
  }
});

test("candidate reads preserve cancellation before fetch, during headers and a late body completion", async () => {
  const stopped = new AbortController(), reason = new DOMException("stop", "AbortError"); stopped.abort(reason); let calls = 0;
  await assert.rejects(createApiClient(configured, async () => { calls++; return Response.json(fixture()); }).getFootCrossingCandidates(jobId, { signal: stopped.signal }), error => error === reason);
  assert.equal(calls, 0);
  const headers = new AbortController(); let release;
  const waitHeaders = new Promise(resolve => { release = resolve; });
  const pending = createApiClient(configured, async () => waitHeaders).getFootCrossingCandidates(jobId, { signal: headers.signal });
  headers.abort(reason); release(Response.json(fixture())); await assert.rejects(pending, error => error === reason);
  const body = new AbortController(); let cancelled = false, controller;
  const response = new Response(new ReadableStream({ start(value) { controller = value; }, cancel() { cancelled = true; } }));
  const reading = createApiClient(configured, async () => response).getFootCrossingCandidates(jobId, { signal: body.signal });
  await Promise.resolve(); await Promise.resolve();
  controller.enqueue(new TextEncoder().encode('{"schema_version":')); body.abort(reason);
  await assert.rejects(reading, error => error === reason); assert.equal(cancelled, true);
});

test("candidate body timeout retains the real 20-second deadline and exact timeout reason", async t => {
  const deadline = new AbortController(), reason = new DOMException("deadline", "TimeoutError");
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 20000); return deadline.signal; });
  let cancelled = false;
  const response = new Response(new ReadableStream({ cancel() { cancelled = true; } }));
  const pending = createApiClient(configured, async () => response).getFootCrossingCandidates(jobId);
  await Promise.resolve(); await Promise.resolve(); deadline.abort(reason);
  await assert.rejects(pending, error => error === reason); assert.equal(cancelled, true);
});
