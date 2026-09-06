(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.RallyMateScoringTruthEventCollectionCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const WORKBENCH_VERSION = "scoring-truth-event-collection-workbench-v1.0.0";
  const STATE_VERSION = "scoring-truth-event-collection-state-v1.0.0";
  const SUBMISSION_VERSION = "scoring-truth-event-annotation-submission-v1.0.0";
  const ADJUDICATION_VERSION = "scoring-truth-event-adjudication-submission-v1.0.0";
  const AUTHORIZATION_STATUS = "operator_reviewed_local_release_verified";
  const EXECUTION_BUNDLE_STATUS = "annotation_execution_ready_operator_release_verified";
  const ADJUDICATION_BUNDLE_STATUS = "adjudication_ready_both_submissions_verified";
  const ANNOTATION_SUBMISSION_STATUS = "full_video_event_phase_annotation_submitted";
  const ADJUDICATION_SUBMISSION_STATUS = "event_phase_adjudication_finalized";
  const EVENT_CODES = Object.freeze(["FS01", "FS02", "FS09"]);
  const PHASE_KEYS_BY_EVENT = Object.freeze({
    FS01: Object.freeze([
      "preload_ms",
      "takeoff_proxy_ms",
      "landing_proxy_ms",
      "redistribution_ms",
      "initiation_ms",
    ]),
    FS02: Object.freeze([
      "direction_conversion_ms",
      "support_extension_proxy_ms",
      "lead_foot_motion_onset_proxy_ms",
      "first_step_slowdown_proxy_ms",
    ]),
    FS09: Object.freeze([
      "peak_speed_ms",
      "deceleration_peak_ms",
      "restabilization_onset_ms",
      "stable_control_onset_ms",
    ]),
  });
  const SHA_RE = /^[0-9A-F]{64}$/;
  const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
  const UTC_RE = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{3})Z$/;
  const PHASE_STATUS = new Set(["observed", "unobservable"]);
  const DECISION_STATUS = new Set(["accepted_event", "rejected_sources", "c_added_event"]);
  const SOURCE_RELATION = new Set(["supports", "merge_source", "split_source", "rejected_source"]);

  const COMMON_BOOT_KEYS = Object.freeze([
    "schema_version",
    "workbench_version",
    "bundle_version",
    "execution_id",
    "bundle_status",
    "authorization_status",
    "authorization_binding_sha256",
    "role_slot",
    "annotation_execution_authorized",
    "operator_release_record_verified",
    "mutation_enabled",
    "import_enabled",
    "export_enabled",
    "full_video_only",
    "machine_event_boundaries_embedded",
    "phase_values_embedded",
    "machine_keypoints_embedded",
    "grades_or_thresholds_supported",
    "calibration_authorized",
    "promotion_authorized",
    "production_enabled",
    "maturity_promoted",
    "event_codes",
    "phase_keys_by_event",
    "required_annotator_slots",
    "reviewer_slot",
    "tasks",
  ]);
  const BUNDLE_REF_KEYS = Object.freeze(["bundle_id", "manifest_binding_sha256"]);
  const EXPECTED_SOURCE_KEYS = Object.freeze([
    "submission_id",
    "raw_sha256",
    "submission_revision_sha256",
    "annotator_id",
  ]);
  const TASK_KEYS = Object.freeze([
    "task_id",
    "video_id",
    "media_path",
    "media_sha256",
    "duration_ms",
    "frame_rate",
    "full_video_review_required",
  ]);
  const EVENT_DECISION_KEYS = Object.freeze([
    "annotation_id",
    "event_code",
    "start_ms",
    "end_ms",
    "phase_observations",
    "confidence_milli",
    "boundary_uncertainty_ms",
    "notes",
  ]);
  const FINAL_EVENT_KEYS = Object.freeze([
    "event_id",
    "event_code",
    "start_ms",
    "end_ms",
    "phase_observations",
    "confidence_milli",
    "boundary_uncertainty_ms",
    "notes",
  ]);
  const PHASE_KEYS = Object.freeze(["status", "timestamp_ms", "reason"]);
  const REVIEW_KEYS = Object.freeze(["completed", "notes"]);
  const STORED_VIDEO_ADJUDICATION_KEYS = Object.freeze([
    "completed",
    "notes",
    "adjudicated_at",
    "source_video_revisions",
  ]);
  const SOURCE_REF_KEYS = Object.freeze([
    "role_slot",
    "annotation_id",
    "annotation_revision_sha256",
    "relation",
  ]);
  const ADJUDICATION_INPUT_KEYS = Object.freeze([
    "adjudication_id",
    "video_id",
    "decision_status",
    "source_annotation_revisions",
    "event",
    "decision_reason",
  ]);

  function fail(message) {
    throw new Error(message);
  }

  function isPlainObject(value) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
    const proto = Object.getPrototypeOf(value);
    return proto === Object.prototype || proto === null;
  }

  function exactKeys(value, expected, name) {
    if (!isPlainObject(value)) fail(`${name} must be an object`);
    const actual = Object.keys(value).sort();
    const wanted = [...expected].sort();
    if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
      fail(`${name} keys must exactly equal: ${wanted.join(",")}`);
    }
    return value;
  }

  function compareCodepoints(left, right) {
    return left < right ? -1 : left > right ? 1 : 0;
  }

  function hasLoneSurrogate(value) {
    for (let index = 0; index < value.length; index += 1) {
      const code = value.charCodeAt(index);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = value.charCodeAt(index + 1);
        if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
        index += 1;
      } else if (code >= 0xdc00 && code <= 0xdfff) return true;
    }
    return false;
  }

  function validateJsonScalarTree(value, path = "value") {
    if (value === null || typeof value === "boolean") return;
    if (typeof value === "string") {
      if (hasLoneSurrogate(value)) fail(`${path} contains an isolated surrogate`);
      return;
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value) || Object.is(value, -0)) fail(`${path} must be a finite canonical number`);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => validateJsonScalarTree(item, `${path}[${index}]`));
      return;
    }
    if (!isPlainObject(value)) fail(`${path} is not JSON-compatible`);
    Object.keys(value).forEach((key) => {
      if (hasLoneSurrogate(key)) fail(`${path} has an invalid object key`);
      validateJsonScalarTree(value[key], `${path}.${key}`);
    });
  }

  function canonicalJson(value) {
    validateJsonScalarTree(value);
    function encode(item) {
      if (Array.isArray(item)) return `[${item.map(encode).join(",")}]`;
      if (isPlainObject(item)) {
        return `{${Object.keys(item).sort().map((key) => `${JSON.stringify(key)}:${encode(item[key])}`).join(",")}}`;
      }
      return JSON.stringify(item);
    }
    return encode(value);
  }

  function clone(value) {
    return JSON.parse(canonicalJson(value));
  }

  function textEncoder() {
    if (typeof TextEncoder !== "undefined") return new TextEncoder();
    if (typeof require === "function") return new (require("util").TextEncoder)();
    fail("TextEncoder is unavailable");
  }

  function cryptoProvider() {
    if (typeof globalThis !== "undefined" && globalThis.crypto && globalThis.crypto.subtle) return globalThis.crypto;
    if (typeof require === "function") return require("crypto").webcrypto;
    fail("WebCrypto is unavailable");
  }

  async function sha256Text(text) {
    const digest = await cryptoProvider().subtle.digest("SHA-256", textEncoder().encode(text));
    return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("").toUpperCase();
  }

  async function sha256Bytes(bytes) {
    let view;
    if (bytes instanceof Uint8Array) view = bytes;
    else if (bytes instanceof ArrayBuffer) view = new Uint8Array(bytes);
    else fail("hash input must be an ArrayBuffer or Uint8Array");
    const digest = await cryptoProvider().subtle.digest("SHA-256", view);
    return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("").toUpperCase();
  }

  async function sha256Canonical(value) {
    return sha256Text(canonicalJson(value));
  }

  function requireSha(value, name) {
    if (typeof value !== "string" || !SHA_RE.test(value)) fail(`${name} must be an uppercase SHA-256`);
    return value;
  }

  function requireId(value, name) {
    if (typeof value !== "string" || !ID_RE.test(value)) fail(`${name} is invalid`);
    return value;
  }

  function canonicalParticipantId(value) {
    if (typeof value !== "string") fail("participant identity must be a string");
    const canonical = value.trim().normalize("NFKC");
    requireId(canonical, "participant identity");
    return canonical;
  }

  function normalizeIdentity(value) {
    return canonicalParticipantId(value).toLowerCase();
  }

  function requireTimestamp(value, name) {
    const parsed = typeof value === "string" ? Date.parse(value) : NaN;
    if (typeof value !== "string" || !UTC_RE.test(value) || !Number.isFinite(parsed) || new Date(parsed).toISOString() !== value) {
      fail(`${name} must be a canonical UTC millisecond timestamp`);
    }
    return value;
  }

  function nextTimestamp(previous, proposed) {
    requireTimestamp(proposed, "proposed timestamp");
    if (!previous) return proposed;
    requireTimestamp(previous, "previous timestamp");
    const previousMs = Date.parse(previous);
    const proposedMs = Date.parse(proposed);
    return proposedMs > previousMs ? proposed : new Date(previousMs + 1).toISOString();
  }

  function nowTimestamp() {
    return new Date().toISOString();
  }

  function sameArray(left, right) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((item, index) => item === right[index]);
  }

  function safeRelativePath(value, name) {
    if (
      typeof value !== "string"
      || !value
      || value.includes("\\")
      || value.startsWith("/")
      || value.split("/").some((part) => !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(part))
    ) {
      fail(`${name} must be a portable relative path`);
    }
    return value;
  }

  function validateBundleRef(value, name) {
    exactKeys(value, BUNDLE_REF_KEYS, name);
    requireId(value.bundle_id, `${name}.bundle_id`);
    requireSha(value.manifest_binding_sha256, `${name}.manifest_binding_sha256`);
    return value;
  }

  function validateExpectedSource(value, slot) {
    exactKeys(value, EXPECTED_SOURCE_KEYS, `bootstrap.expected_source_submissions.${slot}`);
    requireId(value.submission_id, `bootstrap.expected_source_submissions.${slot}.submission_id`);
    requireSha(value.raw_sha256, `bootstrap.expected_source_submissions.${slot}.raw_sha256`);
    requireSha(value.submission_revision_sha256, `bootstrap.expected_source_submissions.${slot}.submission_revision_sha256`);
    normalizeIdentity(value.annotator_id);
    return value;
  }

  function currentBundle(boot) {
    return boot.role_slot === "C" ? boot.adjudication_bundle : boot.execution_bundle;
  }

  function validateBootstrap(boot) {
    if (!isPlainObject(boot)) fail("bootstrap must be an object");
    const roleSlot = boot.role_slot;
    const conditional = roleSlot === "C"
      ? ["adjudication_bundle", "source_execution_bundles", "expected_source_submissions"]
      : ["execution_bundle"];
    exactKeys(boot, [...COMMON_BOOT_KEYS, ...conditional], "bootstrap");
    if (boot.schema_version !== "1.0.0") fail("bootstrap schema_version is unsupported");
    if (boot.workbench_version !== WORKBENCH_VERSION) fail("bootstrap workbench_version is unsupported");
    if (typeof boot.bundle_version !== "string" || !boot.bundle_version) fail("bootstrap bundle_version is invalid");
    requireId(boot.execution_id, "bootstrap execution_id");
    if (!["A", "B", "C"].includes(roleSlot)) fail("bootstrap role_slot must be A, B, or C");
    if (roleSlot === "C") {
      validateBundleRef(boot.adjudication_bundle, "bootstrap.adjudication_bundle");
      exactKeys(boot.source_execution_bundles, ["A", "B"], "bootstrap.source_execution_bundles");
      exactKeys(boot.expected_source_submissions, ["A", "B"], "bootstrap.expected_source_submissions");
      ["A", "B"].forEach((slot) => {
        validateBundleRef(boot.source_execution_bundles[slot], `bootstrap.source_execution_bundles.${slot}`);
        validateExpectedSource(boot.expected_source_submissions[slot], slot);
      });
      if (normalizeIdentity(boot.expected_source_submissions.A.annotator_id) === normalizeIdentity(boot.expected_source_submissions.B.annotator_id)) fail("expected A and B annotator identities must be distinct");
    } else {
      validateBundleRef(boot.execution_bundle, "bootstrap.execution_bundle");
    }
    const expectedBundleStatus = boot.role_slot === "C" ? ADJUDICATION_BUNDLE_STATUS : EXECUTION_BUNDLE_STATUS;
    if (boot.bundle_status !== expectedBundleStatus) fail(`bootstrap bundle_status must be ${expectedBundleStatus}`);
    if (boot.authorization_status !== AUTHORIZATION_STATUS) fail("bootstrap authorization_status is invalid");
    requireSha(boot.authorization_binding_sha256, "bootstrap authorization_binding_sha256");
    const exactSafety = {
      annotation_execution_authorized: true,
      operator_release_record_verified: true,
      mutation_enabled: true,
      import_enabled: boot.role_slot === "C",
      export_enabled: true,
      full_video_only: true,
      machine_event_boundaries_embedded: false,
      phase_values_embedded: false,
      machine_keypoints_embedded: false,
      grades_or_thresholds_supported: false,
      calibration_authorized: false,
      promotion_authorized: false,
      production_enabled: false,
      maturity_promoted: false,
    };
    Object.entries(exactSafety).forEach(([key, expected]) => {
      if (boot[key] !== expected) fail(`bootstrap ${key} is invalid for role ${boot.role_slot}`);
    });
    if (!sameArray(boot.event_codes, EVENT_CODES)) fail("bootstrap event_codes drifted");
    exactKeys(boot.phase_keys_by_event, EVENT_CODES, "bootstrap phase_keys_by_event");
    EVENT_CODES.forEach((code) => {
      if (!sameArray(boot.phase_keys_by_event[code], PHASE_KEYS_BY_EVENT[code])) fail(`bootstrap phases drifted for ${code}`);
    });
    if (!sameArray(boot.required_annotator_slots, ["A", "B"]) || boot.reviewer_slot !== "C") fail("bootstrap role protocol drifted");
    if (!Array.isArray(boot.tasks) || boot.tasks.length !== 3) fail("bootstrap must contain exactly three full-video tasks");
    const taskIds = new Set();
    const videoIds = new Set();
    boot.tasks.forEach((task, index) => {
      exactKeys(task, TASK_KEYS, `bootstrap.tasks[${index}]`);
      requireId(task.task_id, `bootstrap.tasks[${index}].task_id`);
      requireId(task.video_id, `bootstrap.tasks[${index}].video_id`);
      safeRelativePath(task.media_path, `bootstrap.tasks[${index}].media_path`);
      requireSha(task.media_sha256, `bootstrap.tasks[${index}].media_sha256`);
      if (!Number.isSafeInteger(task.duration_ms) || task.duration_ms <= 0) fail(`bootstrap.tasks[${index}].duration_ms is invalid`);
      exactKeys(task.frame_rate, ["numerator", "denominator"], `bootstrap.tasks[${index}].frame_rate`);
      if (!Number.isSafeInteger(task.frame_rate.numerator) || task.frame_rate.numerator <= 0 || !Number.isSafeInteger(task.frame_rate.denominator) || task.frame_rate.denominator <= 0) fail(`bootstrap.tasks[${index}].frame_rate is invalid`);
      if (task.full_video_review_required !== true) fail(`bootstrap.tasks[${index}] must require full-video review`);
      if (taskIds.has(task.task_id) || videoIds.has(task.video_id)) fail("bootstrap task/video IDs must be unique");
      taskIds.add(task.task_id);
      videoIds.add(task.video_id);
    });
    return boot;
  }

  function taskById(boot, taskId) {
    const task = boot.tasks.find((item) => item.task_id === taskId);
    if (!task) fail(`unknown task_id: ${taskId}`);
    return task;
  }

  function taskByVideoId(boot, videoId) {
    const task = boot.tasks.find((item) => item.video_id === videoId);
    if (!task) fail(`unknown video_id: ${videoId}`);
    return task;
  }

  function emptyPhaseObservations(eventCode) {
    if (!EVENT_CODES.includes(eventCode)) return {};
    return Object.fromEntries(PHASE_KEYS_BY_EVENT[eventCode].map((key) => [key, {status: "", timestamp_ms: null, reason: ""}]));
  }

  function emptyEventDecision(annotationId) {
    return {
      annotation_id: annotationId,
      event_code: "",
      start_ms: null,
      end_ms: null,
      phase_observations: {},
      confidence_milli: null,
      boundary_uncertainty_ms: null,
      notes: "",
    };
  }

  function validateEventDecision(decision, boot, task, options = {}) {
    const final = options.final === true;
    const idField = final ? "event_id" : "annotation_id";
    exactKeys(decision, final ? FINAL_EVENT_KEYS : EVENT_DECISION_KEYS, final ? "final event" : "annotation event");
    requireId(decision[idField], `${idField}`);
    const expectedRole = final ? "C" : (options.sourceRole || boot.role_slot);
    if (!decision[idField].startsWith(`m93:${expectedRole}:`)) fail(`${idField} must start with m93:${expectedRole}:`);
    if (!EVENT_CODES.includes(decision.event_code)) fail(`${idField}.event_code is invalid`);
    if (!Number.isSafeInteger(decision.start_ms) || decision.start_ms < 0 || !Number.isSafeInteger(decision.end_ms) || decision.end_ms <= decision.start_ms) fail(`${idField} boundaries must be ordered integer milliseconds`);
    if (task && decision.end_ms > task.duration_ms) fail(`${idField} exceeds the full-video duration`);
    if (!Number.isSafeInteger(decision.confidence_milli) || decision.confidence_milli < 0 || decision.confidence_milli > 1000) fail(`${idField}.confidence_milli must be an integer in 0..1000`);
    if (!Number.isSafeInteger(decision.boundary_uncertainty_ms) || decision.boundary_uncertainty_ms < 0) fail(`${idField}.boundary_uncertainty_ms must be a non-negative integer`);
    if (typeof decision.notes !== "string") fail(`${idField}.notes must be a string`);
    exactKeys(decision.phase_observations, PHASE_KEYS_BY_EVENT[decision.event_code], `${idField}.phase_observations`);
    const fs09Observed = [];
    PHASE_KEYS_BY_EVENT[decision.event_code].forEach((key) => {
      const phase = decision.phase_observations[key];
      exactKeys(phase, PHASE_KEYS, `${idField}.phase_observations.${key}`);
      if (!PHASE_STATUS.has(phase.status)) fail(`${idField}.${key}.status must be observed or unobservable`);
      if (typeof phase.reason !== "string") fail(`${idField}.${key}.reason must be a string`);
      if (phase.status === "observed") {
        if (!Number.isSafeInteger(phase.timestamp_ms) || phase.timestamp_ms < decision.start_ms || phase.timestamp_ms > decision.end_ms) fail(`${idField}.${key}.timestamp_ms must be inside the event`);
        if (phase.reason !== "") fail(`${idField}.${key}.reason must be blank when observed`);
        if (decision.event_code === "FS09") fs09Observed.push(phase.timestamp_ms);
      } else {
        if (phase.timestamp_ms !== null || !phase.reason.trim()) fail(`${idField}.${key} unobservable requires null timestamp_ms and a reason`);
      }
    });
    if (decision.event_code === "FS09" && fs09Observed.some((value, index) => index > 0 && value < fs09Observed[index - 1])) fail("FS09 observed phase timestamps must be monotonic");
    return decision;
  }

  function decisionWithoutId(decision, idField) {
    return Object.fromEntries(Object.keys(decision).filter((key) => key !== idField).map((key) => [key, decision[key]]));
  }

  function saveEventDraft(previous, decision, boot, task, proposedNow) {
    validateEventDecision(decision, boot, task);
    const priorDecision = previous ? Object.fromEntries(EVENT_DECISION_KEYS.map((key) => [key, previous[key]])) : null;
    const unchanged = priorDecision && canonicalJson(priorDecision) === canonicalJson(decision);
    const annotatedAt = unchanged && previous.annotated_at ? previous.annotated_at : nextTimestamp(previous && previous.annotated_at, proposedNow || nowTimestamp());
    return {...clone(decision), annotated_at: annotatedAt};
  }

  function saveReviewDraft(previous, review, proposedNow) {
    exactKeys(review, REVIEW_KEYS, "full-video review");
    if (review.completed !== true) fail("full-video review must be explicitly completed");
    if (typeof review.notes !== "string") fail("full-video review notes must be a string");
    const prior = previous ? {completed: previous.completed, notes: previous.notes} : null;
    const unchanged = prior && canonicalJson(prior) === canonicalJson(review);
    const reviewedAt = unchanged && previous.reviewed_at ? previous.reviewed_at : nextTimestamp(previous && previous.reviewed_at, proposedNow || nowTimestamp());
    return {...clone(review), reviewed_at: reviewedAt};
  }

  function createState(boot, participantId) {
    validateBootstrap(boot);
    const normalized = normalizeIdentity(participantId);
    const videos = Object.fromEntries(boot.tasks.map((task) => [task.task_id, {events: {}, full_video_review: null, selected_annotation_id: ""}]));
    const videoAdjudications = Object.fromEntries(boot.tasks.map((task) => [task.task_id, null]));
    return {
      state_version: STATE_VERSION,
      execution_id: boot.execution_id,
      bundle_id: currentBundle(boot).bundle_id,
      manifest_binding_sha256: currentBundle(boot).manifest_binding_sha256,
      authorization_binding_sha256: boot.authorization_binding_sha256,
      role_slot: boot.role_slot,
      participant_id: canonicalParticipantId(participantId),
      participant_identity_key: normalized,
      videos,
      imports: {A: null, B: null},
      adjudications: {},
      video_adjudications: videoAdjudications,
      last_submission: null,
      last_adjudication_submission: null,
    };
  }

  function validateRestoredState(boot, participantId, state) {
    const expected = createState(boot, participantId);
    exactKeys(state, Object.keys(expected), "stored state");
    for (const key of ["state_version", "execution_id", "bundle_id", "manifest_binding_sha256", "authorization_binding_sha256", "role_slot", "participant_identity_key"]) {
      if (state[key] !== expected[key]) fail(`stored state ${key} does not match this workbench context`);
    }
    if (state.participant_id !== canonicalParticipantId(participantId)) fail("stored state participant_id does not match");
    exactKeys(state.videos, boot.tasks.map((task) => task.task_id), "stored state videos");
    boot.tasks.forEach((task) => {
      const video = state.videos[task.task_id];
      exactKeys(video, ["events", "full_video_review", "selected_annotation_id"], `stored state video ${task.task_id}`);
      if (!isPlainObject(video.events)) fail(`stored state events are invalid for ${task.task_id}`);
      Object.entries(video.events).forEach(([id, event]) => {
        if (id !== event.annotation_id) fail(`stored event key mismatch for ${id}`);
        validateEventDecision(Object.fromEntries(EVENT_DECISION_KEYS.map((key) => [key, event[key]])), boot, task);
        requireTimestamp(event.annotated_at, `stored event ${id}.annotated_at`);
      });
      if (video.full_video_review !== null) {
        const review = video.full_video_review;
        exactKeys(review, ["completed", "notes", "reviewed_at"], `stored review ${task.task_id}`);
        if (review.completed !== true || typeof review.notes !== "string") fail(`stored review ${task.task_id} is invalid`);
        requireTimestamp(review.reviewed_at, `stored review ${task.task_id}.reviewed_at`);
      }
    });
    exactKeys(state.imports, ["A", "B"], "stored imports");
    if (!isPlainObject(state.adjudications)) fail("stored adjudications must be an object");
    exactKeys(state.video_adjudications, boot.tasks.map((task) => task.task_id), "stored video adjudications");
    boot.tasks.forEach((task) => {
      const review = state.video_adjudications[task.task_id];
      if (review === null) return;
      if (boot.role_slot !== "C") fail("A/B state cannot contain video adjudication reviews");
      exactKeys(review, STORED_VIDEO_ADJUDICATION_KEYS, `stored video adjudication ${task.task_id}`);
      if (review.completed !== true || typeof review.notes !== "string") fail(`stored video adjudication ${task.task_id} is invalid`);
      requireTimestamp(review.adjudicated_at, `stored video adjudication ${task.task_id}.adjudicated_at`);
      exactKeys(review.source_video_revisions, ["A", "B"], `stored video adjudication ${task.task_id}.source_video_revisions`);
      requireSha(review.source_video_revisions.A, `stored video adjudication ${task.task_id}.source A revision`);
      requireSha(review.source_video_revisions.B, `stored video adjudication ${task.task_id}.source B revision`);
    });
    return state;
  }

  async function storageKey(boot, roleSlot, participantId) {
    validateBootstrap(boot);
    if (roleSlot !== boot.role_slot) fail("storage role does not match bootstrap role");
    const identityHash = await sha256Text(normalizeIdentity(participantId));
    return `rallymate:m93:event-phase:${boot.execution_id}:${currentBundle(boot).bundle_id}:${currentBundle(boot).manifest_binding_sha256}:${boot.authorization_binding_sha256}:${STATE_VERSION}:${roleSlot}:${identityHash}`;
  }

  function annotationRevisionPayload(boot, state, task, rowWithoutSha) {
    return {
      execution_id: boot.execution_id,
      execution_bundle: boot.role_slot === "C" ? boot.source_execution_bundles[state.role_slot] : boot.execution_bundle,
      authorization_binding_sha256: boot.authorization_binding_sha256,
      role_slot: state.role_slot,
      annotator_id: state.participant_id,
      task_id: task.task_id,
      video_id: task.video_id,
      annotation: rowWithoutSha,
    };
  }

  async function buildAnnotationEvent(boot, state, task, draft) {
    const decision = Object.fromEntries(EVENT_DECISION_KEYS.map((key) => [key, draft[key]]));
    validateEventDecision(decision, boot, task);
    requireTimestamp(draft.annotated_at, `${decision.annotation_id}.annotated_at`);
    const row = {
      ...clone(decision),
      event_id: decision.annotation_id,
      annotated_at: draft.annotated_at,
    };
    const ordered = {
      annotation_id: row.annotation_id,
      event_id: row.event_id,
      event_code: row.event_code,
      start_ms: row.start_ms,
      end_ms: row.end_ms,
      phase_observations: row.phase_observations,
      confidence_milli: row.confidence_milli,
      boundary_uncertainty_ms: row.boundary_uncertainty_ms,
      notes: row.notes,
      annotated_at: row.annotated_at,
    };
    return {...ordered, annotation_revision_sha256: await sha256Canonical(annotationRevisionPayload(boot, state, task, ordered))};
  }

  async function buildReview(boot, state, task, review) {
    if (!review || review.completed !== true) fail(`full-video review is incomplete: ${task.video_id}`);
    exactKeys(review, ["completed", "notes", "reviewed_at"], `full-video review ${task.video_id}`);
    requireTimestamp(review.reviewed_at, `full-video review ${task.video_id}.reviewed_at`);
    const base = clone(review);
    const payload = {
      execution_id: boot.execution_id,
      execution_bundle: boot.role_slot === "C" ? boot.source_execution_bundles[state.role_slot] : boot.execution_bundle,
      authorization_binding_sha256: boot.authorization_binding_sha256,
      role_slot: state.role_slot,
      annotator_id: state.participant_id,
      task_id: task.task_id,
      video_id: task.video_id,
      full_video_review: base,
    };
    return {...base, review_revision_sha256: await sha256Canonical(payload)};
  }

  function submissionDecisionKey(submission) {
    if (!submission) return "";
    return canonicalJson({
      execution_id: submission.execution_id,
      execution_bundle: submission.execution_bundle,
      authorization_binding_sha256: submission.authorization_binding_sha256,
      role_slot: submission.role_slot,
      annotator_id: submission.annotator_id,
      video_revisions: submission.videos.map((video) => ({video_id: video.video_id, video_revision_sha256: video.video_revision_sha256})),
    });
  }

  async function annotationSubmissionId(boot, roleSlot, annotatorId) {
    const executionBundle = boot.role_slot === "C" ? boot.source_execution_bundles[roleSlot] : boot.execution_bundle;
    const digest = await sha256Canonical({
      execution_id: boot.execution_id,
      execution_bundle: executionBundle,
      authorization_binding_sha256: boot.authorization_binding_sha256,
      role_slot: roleSlot,
      annotator_identity_key: normalizeIdentity(annotatorId),
    });
    return `submission-${digest}`;
  }

  async function buildAnnotationSubmission(boot, state, options = {}) {
    validateBootstrap(boot);
    if (!["A", "B"].includes(state.role_slot) || state.role_slot !== boot.role_slot) fail("annotation submission requires role A or B");
    validateRestoredState(boot, state.participant_id, state);
    const videos = [];
    for (const task of boot.tasks) {
      const videoState = state.videos[task.task_id];
      const fullReview = await buildReview(boot, state, task, videoState.full_video_review);
      const events = [];
      for (const draft of Object.values(videoState.events)) events.push(await buildAnnotationEvent(boot, state, task, draft));
      events.sort((left, right) => left.start_ms - right.start_ms || compareCodepoints(left.annotation_id, right.annotation_id));
      if (events.some((event) => Date.parse(event.annotated_at) > Date.parse(fullReview.reviewed_at))) fail(`event annotation is newer than full-video review: ${task.video_id}`);
      const videoBase = {task_id: task.task_id, video_id: task.video_id, full_video_review: fullReview, events};
      const videoRevision = await sha256Canonical({
        execution_id: boot.execution_id,
        execution_bundle: boot.execution_bundle,
        authorization_binding_sha256: boot.authorization_binding_sha256,
        role_slot: state.role_slot,
        annotator_id: state.participant_id,
        ...videoBase,
      });
      videos.push({...videoBase, video_revision_sha256: videoRevision});
    }
    const now = options.now || nowTimestamp();
    requireTimestamp(now, "export time");
    const previous = options.previousSubmission || state.last_submission;
    const provisional = {
      schema_version: "1.0.0",
      submission_version: SUBMISSION_VERSION,
      status: ANNOTATION_SUBMISSION_STATUS,
      artifact_scope: "independent_full_video_event_phase_annotation",
      execution_id: boot.execution_id,
      execution_bundle: clone(boot.execution_bundle),
      authorization_binding_sha256: boot.authorization_binding_sha256,
      submission_id: await annotationSubmissionId(boot, state.role_slot, state.participant_id),
      role_slot: state.role_slot,
      annotator_id: state.participant_id,
      videos,
    };
    const probe = {...provisional, submitted_at: previous && previous.submitted_at ? previous.submitted_at : now, submission_revision_sha256: "0".repeat(64), exported_at: now};
    const unchanged = previous && submissionDecisionKey(previous) === submissionDecisionKey(probe);
    const submittedAt = unchanged ? previous.submitted_at : nextTimestamp(previous && previous.submitted_at, now);
    if (videos.some((video) => Date.parse(video.full_video_review.reviewed_at) > Date.parse(submittedAt))) fail("annotation submission time predates a full-video review");
    const revisionPayload = {...provisional, submitted_at: submittedAt};
    const submissionRevision = await sha256Canonical(revisionPayload);
    const exportedAt = nextTimestamp(previous && previous.exported_at, now);
    return {...revisionPayload, submission_revision_sha256: submissionRevision, exported_at: exportedAt};
  }

  async function validateAnnotationSubmission(boot, submission, options = {}) {
    const topKeys = [
      "schema_version", "submission_version", "status", "artifact_scope", "execution_id",
      "execution_bundle", "authorization_binding_sha256", "submission_id", "role_slot",
      "annotator_id", "videos", "submitted_at", "submission_revision_sha256", "exported_at",
    ];
    exactKeys(submission, topKeys, "annotation submission");
    if (submission.schema_version !== "1.0.0" || submission.submission_version !== SUBMISSION_VERSION || submission.status !== ANNOTATION_SUBMISSION_STATUS || submission.artifact_scope !== "independent_full_video_event_phase_annotation") fail("annotation submission version/status/scope is invalid");
    if (!["A", "B"].includes(submission.role_slot)) fail("annotation submission role_slot must be A or B");
    const expectedExecutionBundle = boot.role_slot === "C" ? boot.source_execution_bundles[submission.role_slot] : boot.execution_bundle;
    validateBundleRef(submission.execution_bundle, "annotation submission execution_bundle");
    if (submission.execution_id !== boot.execution_id || canonicalJson(submission.execution_bundle) !== canonicalJson(expectedExecutionBundle) || submission.authorization_binding_sha256 !== boot.authorization_binding_sha256) fail("annotation submission lineage does not match its execution bundle");
    normalizeIdentity(submission.annotator_id);
    requireId(submission.submission_id, "annotation submission submission_id");
    if (submission.submission_id !== await annotationSubmissionId(boot, submission.role_slot, submission.annotator_id)) fail("annotation submission_id is not deterministic for this lineage and annotator");
    requireTimestamp(submission.submitted_at, "annotation submission submitted_at");
    requireTimestamp(submission.exported_at, "annotation submission exported_at");
    if (Date.parse(submission.exported_at) < Date.parse(submission.submitted_at)) fail("annotation submission exported_at predates submitted_at");
    requireSha(submission.submission_revision_sha256, "annotation submission revision");
    if (!Array.isArray(submission.videos) || submission.videos.length !== boot.tasks.length) fail("annotation submission must cover all three videos");
    const seenAnnotations = new Set();
    for (let index = 0; index < boot.tasks.length; index += 1) {
      const task = boot.tasks[index];
      const video = submission.videos[index];
      exactKeys(video, ["task_id", "video_id", "full_video_review", "events", "video_revision_sha256"], `annotation submission videos[${index}]`);
      if (video.task_id !== task.task_id || video.video_id !== task.video_id) fail("annotation submission video order/scope drifted");
      requireSha(video.video_revision_sha256, `annotation submission ${video.video_id} revision`);
      const review = video.full_video_review;
      exactKeys(review, ["completed", "reviewed_at", "notes", "review_revision_sha256"], `annotation submission review ${video.video_id}`);
      if (review.completed !== true || typeof review.notes !== "string") fail(`annotation submission review is incomplete: ${video.video_id}`);
      requireTimestamp(review.reviewed_at, `annotation submission review ${video.video_id}.reviewed_at`);
      if (Date.parse(review.reviewed_at) > Date.parse(submission.submitted_at)) fail(`annotation submission review time exceeds submitted_at: ${video.video_id}`);
      requireSha(review.review_revision_sha256, `annotation submission review ${video.video_id}.revision`);
      const reviewBase = {completed: review.completed, notes: review.notes, reviewed_at: review.reviewed_at};
      const expectedReview = await buildReview(boot, {role_slot: submission.role_slot, participant_id: submission.annotator_id}, task, reviewBase);
      if (expectedReview.review_revision_sha256 !== review.review_revision_sha256) fail(`annotation submission review revision mismatch: ${video.video_id}`);
      if (!Array.isArray(video.events)) fail(`annotation submission events must be an array: ${video.video_id}`);
      let priorSort = null;
      for (const event of video.events) {
        const eventKeys = [
          "annotation_id", "event_id", "event_code", "start_ms", "end_ms", "phase_observations",
          "confidence_milli", "boundary_uncertainty_ms", "notes", "annotated_at", "annotation_revision_sha256",
        ];
        exactKeys(event, eventKeys, `annotation submission event ${video.video_id}`);
        if (event.event_id !== event.annotation_id) fail("annotation submission event_id must equal annotation_id");
        const decision = Object.fromEntries(EVENT_DECISION_KEYS.map((key) => [key, event[key]]));
        validateEventDecision(decision, boot, task, {sourceRole: submission.role_slot});
        requireTimestamp(event.annotated_at, `${event.annotation_id}.annotated_at`);
        requireSha(event.annotation_revision_sha256, `${event.annotation_id}.annotation_revision_sha256`);
        const rowWithoutSha = Object.fromEntries(eventKeys.filter((key) => key !== "annotation_revision_sha256").map((key) => [key, event[key]]));
        const expectedSha = await sha256Canonical(annotationRevisionPayload(boot, {role_slot: submission.role_slot, participant_id: submission.annotator_id}, task, rowWithoutSha));
        if (expectedSha !== event.annotation_revision_sha256) fail(`annotation revision mismatch: ${event.annotation_id}`);
        const identity = `${submission.role_slot}\u0000${event.annotation_id}`;
        if (seenAnnotations.has(identity)) fail(`duplicate annotation_id in submission: ${event.annotation_id}`);
        seenAnnotations.add(identity);
        const sortKey = `${String(event.start_ms).padStart(16, "0")}\u0000${event.annotation_id}`;
        if (priorSort !== null && sortKey < priorSort) fail(`annotation events are not canonically sorted: ${video.video_id}`);
        priorSort = sortKey;
      }
      if (video.events.some((event) => Date.parse(event.annotated_at) > Date.parse(review.reviewed_at))) fail(`annotation chronology is invalid: ${video.video_id}`);
      const videoBase = {task_id: video.task_id, video_id: video.video_id, full_video_review: video.full_video_review, events: video.events};
      const expectedVideo = await sha256Canonical({
        execution_id: boot.execution_id,
        execution_bundle: expectedExecutionBundle,
        authorization_binding_sha256: boot.authorization_binding_sha256,
        role_slot: submission.role_slot,
        annotator_id: submission.annotator_id,
        ...videoBase,
      });
      if (expectedVideo !== video.video_revision_sha256) fail(`video revision mismatch: ${video.video_id}`);
    }
    const revisionPayload = Object.fromEntries(topKeys.filter((key) => !["submission_revision_sha256", "exported_at"].includes(key)).map((key) => [key, submission[key]]));
    if (await sha256Canonical(revisionPayload) !== submission.submission_revision_sha256) fail("annotation submission revision mismatch");
    if (boot.role_slot === "C") {
      const expected = boot.expected_source_submissions[submission.role_slot];
      requireSha(options.rawSha256, "import raw_sha256");
      if (
        submission.submission_id !== expected.submission_id
        || options.rawSha256 !== expected.raw_sha256
        || submission.submission_revision_sha256 !== expected.submission_revision_sha256
        || submission.annotator_id !== expected.annotator_id
      ) fail(`imported role ${submission.role_slot} submission does not exactly match expected_source_submissions`);
    } else if (submission.role_slot !== boot.role_slot) {
      fail("annotation submission role does not match this execution workbench");
    }
    return submission;
  }

  async function validateAnnotationPair(boot, importRecords, reviewerId) {
    if (boot.role_slot !== "C") fail("annotation pair validation requires role C");
    if (!Array.isArray(importRecords) || importRecords.length !== 2) fail("reviewer requires exactly two annotation submissions");
    const pair = {};
    const rawBySlot = {};
    for (const record of importRecords) {
      exactKeys(record, ["raw_sha256", "submission"], "annotation import record");
      requireSha(record.raw_sha256, "annotation import raw_sha256");
      const submission = record.submission;
      await validateAnnotationSubmission(boot, submission, {rawSha256: record.raw_sha256});
      if (pair[submission.role_slot]) fail(`duplicate annotation role submission: ${submission.role_slot}`);
      pair[submission.role_slot] = submission;
      rawBySlot[submission.role_slot] = record.raw_sha256;
    }
    if (!pair.A || !pair.B) fail("reviewer requires exact role A and role B submissions");
    const aIdentity = normalizeIdentity(pair.A.annotator_id);
    const bIdentity = normalizeIdentity(pair.B.annotator_id);
    if (aIdentity === bIdentity) fail("role A and B annotator identities must be distinct");
    if (reviewerId) {
      const reviewer = normalizeIdentity(reviewerId);
      if (reviewer === aIdentity || reviewer === bIdentity) fail("reviewer identity must differ from A and B");
    }
    pair.raw_sha256_by_slot = rawBySlot;
    return pair;
  }

  function sourceVideoRevisionPair(pair, videoId) {
    const result = {};
    for (const slot of ["A", "B"]) {
      const video = pair[slot].videos.find((item) => item.video_id === videoId);
      if (!video) fail(`source ${slot} does not contain video ${videoId}`);
      result[slot] = video.video_revision_sha256;
    }
    return result;
  }

  function saveVideoAdjudicationReview(previous, review, boot, task, pair, proposedNow) {
    if (boot.role_slot !== "C") fail("video adjudication review requires role C");
    exactKeys(review, REVIEW_KEYS, "video adjudication review");
    if (review.completed !== true) fail("video adjudication review must be explicitly completed");
    if (typeof review.notes !== "string") fail("video adjudication review notes must be a string");
    const sourceVideoRevisions = sourceVideoRevisionPair(pair, task.video_id);
    const previousReview = previous ? {completed: previous.completed, notes: previous.notes} : null;
    const unchanged = previous
      && canonicalJson(previousReview) === canonicalJson(review)
      && canonicalJson(previous.source_video_revisions) === canonicalJson(sourceVideoRevisions);
    const adjudicatedAt = unchanged
      ? previous.adjudicated_at
      : nextTimestamp(previous && previous.adjudicated_at, proposedNow || nowTimestamp());
    return {
      completed: true,
      notes: review.notes,
      adjudicated_at: adjudicatedAt,
      source_video_revisions: sourceVideoRevisions,
    };
  }

  function markStaleVideoAdjudicationReviews(videoAdjudications, boot, pair) {
    exactKeys(videoAdjudications, boot.tasks.map((task) => task.task_id), "video adjudications");
    return Object.fromEntries(boot.tasks.map((task) => {
      const review = videoAdjudications[task.task_id];
      if (review === null) return [task.task_id, null];
      const current = sourceVideoRevisionPair(pair, task.video_id);
      return [task.task_id, canonicalJson(current) === canonicalJson(review.source_video_revisions) ? clone(review) : null];
    }));
  }

  async function buildVideoAdjudicationReview(boot, state, task, pair, review) {
    if (!review) fail(`video adjudication review is incomplete: ${task.video_id}`);
    exactKeys(review, STORED_VIDEO_ADJUDICATION_KEYS, `video adjudication review ${task.video_id}`);
    if (review.completed !== true || typeof review.notes !== "string") fail(`video adjudication review is invalid: ${task.video_id}`);
    requireTimestamp(review.adjudicated_at, `video adjudication review ${task.video_id}.adjudicated_at`);
    const sourceVideoRevisions = sourceVideoRevisionPair(pair, task.video_id);
    if (canonicalJson(review.source_video_revisions) !== canonicalJson(sourceVideoRevisions)) fail(`video adjudication review is stale: ${task.video_id}`);
    const base = {
      task_id: task.task_id,
      video_id: task.video_id,
      completed: true,
      notes: review.notes,
      adjudicated_at: review.adjudicated_at,
    };
    const payload = {
      execution_id: boot.execution_id,
      adjudication_bundle: boot.adjudication_bundle,
      authorization_binding_sha256: boot.authorization_binding_sha256,
      reviewer_slot: "C",
      reviewer_id: state.participant_id,
      source_video_revisions: sourceVideoRevisions,
      video_adjudication: base,
    };
    return {...base, review_revision_sha256: await sha256Canonical(payload)};
  }

  function sourceEventIndex(pair) {
    const index = new Map();
    for (const slot of ["A", "B"]) {
      pair[slot].videos.forEach((video) => video.events.forEach((event) => {
        index.set(`${slot}\u0000${event.annotation_id}`, {slot, video_id: video.video_id, event});
      }));
    }
    return index;
  }

  function validateSourceReference(ref, pair, videoId, index) {
    exactKeys(ref, SOURCE_REF_KEYS, "source annotation revision");
    if (!["A", "B"].includes(ref.role_slot)) fail("source annotation role_slot must be A or B");
    requireId(ref.annotation_id, "source annotation_id");
    requireSha(ref.annotation_revision_sha256, "source annotation revision");
    if (!SOURCE_RELATION.has(ref.relation)) fail("source annotation relation is invalid");
    const source = index.get(`${ref.role_slot}\u0000${ref.annotation_id}`);
    if (!source || source.video_id !== videoId || source.event.annotation_revision_sha256 !== ref.annotation_revision_sha256) fail(`source annotation revision does not match imported ${ref.role_slot}: ${ref.annotation_id}`);
    return ref;
  }

  function validateAdjudicationInput(input, boot, pair) {
    exactKeys(input, ADJUDICATION_INPUT_KEYS, "adjudication decision");
    requireId(input.adjudication_id, "adjudication_id");
    if (!input.adjudication_id.startsWith("m93:C:")) fail("adjudication_id must start with m93:C:");
    taskByVideoId(boot, input.video_id);
    if (!DECISION_STATUS.has(input.decision_status)) fail("adjudication decision_status is invalid");
    if (!Array.isArray(input.source_annotation_revisions)) fail("source_annotation_revisions must be an array");
    if (typeof input.decision_reason !== "string") fail("adjudication decision_reason must be a string");
    const index = sourceEventIndex(pair);
    const seen = new Set();
    input.source_annotation_revisions.forEach((ref) => {
      validateSourceReference(ref, pair, input.video_id, index);
      const key = `${ref.role_slot}\u0000${ref.annotation_id}`;
      if (seen.has(key)) fail(`duplicate source ref inside adjudication: ${ref.annotation_id}`);
      seen.add(key);
    });
    const task = taskByVideoId(boot, input.video_id);
    if (input.decision_status === "rejected_sources") {
      if (input.event !== null || !input.source_annotation_revisions.length || !input.decision_reason.trim()) fail("rejected_sources requires source refs, null event, and a reason");
      if (input.source_annotation_revisions.some((ref) => ref.relation !== "rejected_source")) fail("rejected_sources refs must use rejected_source relation");
    } else {
      if (input.event === null) fail(`${input.decision_status} requires a final event`);
      validateEventDecision(input.event, boot, task, {final: true});
      if (input.decision_status === "accepted_event" && !input.source_annotation_revisions.length) fail("accepted_event requires at least one source annotation revision");
      if (input.decision_status === "c_added_event" && (input.source_annotation_revisions.length || !input.decision_reason.trim())) fail("c_added_event requires zero source refs and a reason");
      if (input.source_annotation_revisions.some((ref) => ref.relation === "rejected_source")) fail("accepted decisions cannot use rejected_source relation");
    }
    return input;
  }

  function saveAdjudicationDraft(previous, input, boot, pair, proposedNow) {
    validateAdjudicationInput(input, boot, pair);
    const sourceVideoRevisions = sourceVideoRevisionPair(pair, input.video_id);
    const priorInput = previous ? Object.fromEntries(ADJUDICATION_INPUT_KEYS.map((key) => [key, previous[key]])) : null;
    const unchanged = previous && priorInput && canonicalJson(priorInput) === canonicalJson(input) && canonicalJson(previous.source_video_revisions) === canonicalJson(sourceVideoRevisions) && previous.stale !== true;
    const adjudicatedAt = unchanged ? previous.adjudicated_at : nextTimestamp(previous && previous.adjudicated_at, proposedNow || nowTimestamp());
    return {...clone(input), source_video_revisions: sourceVideoRevisions, adjudicated_at: adjudicatedAt, stale: false};
  }

  function markStaleAdjudications(adjudications, pair) {
    if (!isPlainObject(adjudications)) fail("adjudications must be an object");
    return Object.fromEntries(Object.entries(adjudications).map(([id, draft]) => {
      const current = sourceVideoRevisionPair(pair, draft.video_id);
      const stale = canonicalJson(current) !== canonicalJson(draft.source_video_revisions);
      return [id, {...clone(draft), stale}];
    }));
  }

  function coverageReport(adjudications, pair) {
    const index = sourceEventIndex(pair);
    const occurrences = new Map();
    const staleIds = [];
    Object.entries(adjudications).forEach(([id, draft]) => {
      if (draft.stale === true) staleIds.push(id);
      (draft.source_annotation_revisions || []).forEach((ref) => {
        const key = `${ref.role_slot}\u0000${ref.annotation_id}`;
        if (!occurrences.has(key)) occurrences.set(key, []);
        occurrences.get(key).push({adjudication_id: id, relation: ref.relation});
      });
    });
    const missing = [...index.keys()].filter((key) => !occurrences.has(key));
    const invalidDuplicates = [...occurrences.entries()].filter(([, rows]) => rows.length > 1 && rows.some((row) => row.relation !== "split_source")).map(([key]) => key);
    return {complete: !missing.length && !invalidDuplicates.length && !staleIds.length, missing, invalid_duplicates: invalidDuplicates, stale_adjudication_ids: staleIds};
  }

  async function buildAdjudicationDecision(boot, state, draft) {
    requireTimestamp(draft.adjudicated_at, `${draft.adjudication_id}.adjudicated_at`);
    if (draft.stale === true) fail(`adjudication is stale: ${draft.adjudication_id}`);
    const base = {
      adjudication_id: draft.adjudication_id,
      video_id: draft.video_id,
      decision_status: draft.decision_status,
      source_video_revisions: draft.source_video_revisions,
      source_annotation_revisions: draft.source_annotation_revisions,
      event: draft.event,
      decision_reason: draft.decision_reason,
      adjudicated_at: draft.adjudicated_at,
    };
    const payload = {
      execution_id: boot.execution_id,
      adjudication_bundle: boot.adjudication_bundle,
      authorization_binding_sha256: boot.authorization_binding_sha256,
      reviewer_slot: "C",
      reviewer_id: state.participant_id,
      decision: base,
    };
    return {...clone(base), adjudication_revision_sha256: await sha256Canonical(payload)};
  }

  function sourceSubmissionSummary(pair) {
    return Object.fromEntries(["A", "B"].map((slot) => [slot, {
      submission_id: pair[slot].submission_id,
      raw_sha256: pair.raw_sha256_by_slot[slot],
      annotator_id: pair[slot].annotator_id,
      execution_bundle: pair[slot].execution_bundle,
      submission_revision_sha256: pair[slot].submission_revision_sha256,
      video_revision_sha256_by_video: Object.fromEntries(pair[slot].videos.map((video) => [video.video_id, video.video_revision_sha256])),
    }]));
  }

  function adjudicationDecisionKey(submission) {
    if (!submission) return "";
    return canonicalJson({
      execution_id: submission.execution_id,
      adjudication_bundle: submission.adjudication_bundle,
      authorization_binding_sha256: submission.authorization_binding_sha256,
      reviewer_id: submission.reviewer_id,
      source_submissions: submission.source_submissions,
      video_adjudication_revisions: submission.video_adjudications.map((review) => review.review_revision_sha256),
      decision_revisions: submission.decisions.map((decision) => decision.adjudication_revision_sha256),
    });
  }

  async function buildAdjudicationSubmission(boot, state, options = {}) {
    validateBootstrap(boot);
    if (boot.role_slot !== "C" || state.role_slot !== "C") fail("adjudication submission requires role C");
    validateRestoredState(boot, state.participant_id, state);
    const pair = await validateAnnotationPair(boot, [state.imports.A, state.imports.B], state.participant_id);
    const report = coverageReport(state.adjudications, pair);
    if (!report.complete) fail(`adjudication source coverage is incomplete: ${canonicalJson(report)}`);
    const videoAdjudications = [];
    for (const task of boot.tasks) {
      videoAdjudications.push(await buildVideoAdjudicationReview(boot, state, task, pair, state.video_adjudications[task.task_id]));
    }
    const decisions = [];
    const finalEventIds = new Set();
    for (const [storedId, draft] of Object.entries(state.adjudications)) {
      if (storedId !== draft.adjudication_id) fail(`stored adjudication key mismatch: ${storedId}`);
      validateAdjudicationInput(Object.fromEntries(ADJUDICATION_INPUT_KEYS.map((key) => [key, draft[key]])), boot, pair);
      const decision = await buildAdjudicationDecision(boot, state, draft);
      if (decision.event) {
        if (finalEventIds.has(decision.event.event_id)) fail(`duplicate final event_id: ${decision.event.event_id}`);
        finalEventIds.add(decision.event.event_id);
      }
      decisions.push(decision);
    }
    decisions.forEach((decision) => {
      const review = videoAdjudications.find((item) => item.video_id === decision.video_id);
      if (!review || Date.parse(decision.adjudicated_at) > Date.parse(review.adjudicated_at)) fail(`adjudication decision is newer than video adjudication review: ${decision.video_id}`);
    });
    decisions.sort((left, right) => compareCodepoints(left.video_id, right.video_id) || ((left.event && left.event.start_ms) || 0) - ((right.event && right.event.start_ms) || 0) || compareCodepoints(left.adjudication_id, right.adjudication_id));
    const now = options.now || nowTimestamp();
    requireTimestamp(now, "adjudication export time");
    const previous = options.previousSubmission || state.last_adjudication_submission;
    const provisional = {
      schema_version: "1.0.0",
      adjudication_version: ADJUDICATION_VERSION,
      status: ADJUDICATION_SUBMISSION_STATUS,
      artifact_scope: "independent_full_video_event_phase_adjudication",
      execution_id: boot.execution_id,
      adjudication_bundle: clone(boot.adjudication_bundle),
      authorization_binding_sha256: boot.authorization_binding_sha256,
      reviewer_slot: "C",
      reviewer_id: state.participant_id,
      source_submissions: sourceSubmissionSummary(pair),
      video_adjudications: videoAdjudications,
      decisions,
    };
    const probe = {...provisional, adjudicated_at: previous && previous.adjudicated_at ? previous.adjudicated_at : now, adjudication_submission_revision_sha256: "0".repeat(64), exported_at: now};
    const unchanged = previous && adjudicationDecisionKey(previous) === adjudicationDecisionKey(probe);
    const adjudicatedAt = unchanged ? previous.adjudicated_at : nextTimestamp(previous && previous.adjudicated_at, now);
    if (
      videoAdjudications.some((review) => Date.parse(review.adjudicated_at) > Date.parse(adjudicatedAt))
      || decisions.some((decision) => Date.parse(decision.adjudicated_at) > Date.parse(adjudicatedAt))
    ) fail("adjudication submission time predates a decision or video review");
    const revisionPayload = {...provisional, adjudicated_at: adjudicatedAt};
    const revision = await sha256Canonical(revisionPayload);
    const exportedAt = nextTimestamp(previous && previous.exported_at, now);
    return {...revisionPayload, adjudication_submission_revision_sha256: revision, exported_at: exportedAt};
  }

  function flattenEvent(event) {
    if (!event) return {};
    const result = {
      event_code: event.event_code,
      start_ms: event.start_ms,
      end_ms: event.end_ms,
      confidence_milli: event.confidence_milli,
      boundary_uncertainty_ms: event.boundary_uncertainty_ms,
      notes: event.notes,
    };
    Object.entries(event.phase_observations || {}).forEach(([key, phase]) => {
      result[`${key}.status`] = phase.status;
      result[`${key}.timestamp_ms`] = phase.timestamp_ms;
      result[`${key}.reason`] = phase.reason;
    });
    return result;
  }

  function diffEvents(left, right) {
    const a = flattenEvent(left);
    const b = flattenEvent(right);
    return [...new Set([...Object.keys(a), ...Object.keys(b)])].sort().map((field) => ({
      field,
      A: Object.hasOwn(a, field) ? a[field] : null,
      B: Object.hasOwn(b, field) ? b[field] : null,
      different: canonicalJson(Object.hasOwn(a, field) ? a[field] : null) !== canonicalJson(Object.hasOwn(b, field) ? b[field] : null),
    }));
  }

  function serializeCanonicalFile(value) {
    return `${canonicalJson(value)}\n`;
  }

  function parseCanonicalJsonText(text) {
    if (typeof text !== "string" || text.startsWith("\uFEFF") || !text.endsWith("\n") || text.endsWith("\n\n")) fail("import must be one canonical UTF-8 JSON object followed by one newline");
    const body = text.slice(0, -1);
    let value;
    try { value = JSON.parse(body); } catch (_error) { fail("import is not valid JSON"); }
    if (!isPlainObject(value)) fail("import root must be an object");
    if (canonicalJson(value) !== body) fail("import bytes are not canonical JSON; duplicate, reordered, or noncanonical fields are not accepted");
    return value;
  }

  function parseCanonicalJsonBytes(bytes) {
    let view;
    if (bytes instanceof Uint8Array) view = bytes;
    else if (bytes instanceof ArrayBuffer) view = new Uint8Array(bytes);
    else fail("import bytes must be an ArrayBuffer or Uint8Array");
    let text;
    try { text = new TextDecoder("utf-8", {fatal: true}).decode(view); } catch (_error) { fail("import is not valid UTF-8"); }
    return parseCanonicalJsonText(text);
  }

  return Object.freeze({
    WORKBENCH_VERSION,
    STATE_VERSION,
    SUBMISSION_VERSION,
    ADJUDICATION_VERSION,
    AUTHORIZATION_STATUS,
    EXECUTION_BUNDLE_STATUS,
    ADJUDICATION_BUNDLE_STATUS,
    ANNOTATION_SUBMISSION_STATUS,
    ADJUDICATION_SUBMISSION_STATUS,
    EVENT_CODES,
    PHASE_KEYS_BY_EVENT,
    canonicalJson,
    clone,
    sha256Text,
    sha256Bytes,
    sha256Canonical,
    normalizeIdentity,
    nextTimestamp,
    validateBootstrap,
    taskById,
    taskByVideoId,
    emptyPhaseObservations,
    emptyEventDecision,
    validateEventDecision,
    saveEventDraft,
    saveReviewDraft,
    saveVideoAdjudicationReview,
    createState,
    validateRestoredState,
    storageKey,
    buildAnnotationSubmission,
    validateAnnotationSubmission,
    validateAnnotationPair,
    sourceVideoRevisionPair,
    markStaleVideoAdjudicationReviews,
    sourceEventIndex,
    validateAdjudicationInput,
    saveAdjudicationDraft,
    markStaleAdjudications,
    coverageReport,
    buildAdjudicationSubmission,
    diffEvents,
    serializeCanonicalFile,
    parseCanonicalJsonText,
    parseCanonicalJsonBytes,
  });
});
