import { modelMeasurementEvidenceOf, registerModelTransportEvidence } from "./model-measurement";
import { freezeReportTransport } from "./report-transport-state";

/** Lossless backup transport only; neither a measurement nor a scoring gate. */
export const MODEL_PROOF_CODEC = "model-proof-pool-v1";
export const MAX_MODEL_PROOFS = 2048;
export const MAX_MODEL_PROOF_REFERENCES = 20000;
// Repeated validated proofs are shared after decoding; wire size is separately bounded.
export const MAX_EXPANDED_BACKUP_BYTES = 512 * 1024 * 1024;
const MAX_DEPTH = 32, MAX_BASE_NODES = 2_000_000;
const CODEC = "model_evidence_codec", POOL = "model_evidence_pool", REF = "model_evidence_ref";
const API_TRANSPORT = "report_transport";
const PROOF_KEYS = ["measurement_provenance", "source_window", "geometry_mask", "source_track_id"];
const WINDOW_KEYS = ["start_ms", "end_ms", "source_frames", "video_timestamps_ms", "source_timestamps_ms", "source_bindings",
  "person_track_id", "selection_epoch", "camera_reference_epoch", "video_sha256"];
const BINDING_KEYS = ["video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "timestamp_ms",
  "person_track_id", "selection_epoch", "camera_reference_epoch", "video_timestamp_ms"];
const PROVENANCE_KEYS = ["pose_source", "observation_semantics", "assessment_status", "scoring_allowed", "confidence_available", "visibility_verified"];
type ObjectValue = Record<string, unknown>;
type Proof = ObjectValue;
const object = (value: unknown): value is ObjectValue => !!value && typeof value === "object" && !Array.isArray(value);
const own = (value: ObjectValue, key: string) => Object.hasOwn(value, key);
const byteLength = (value: string) => new TextEncoder().encode(value).byteLength;
const fail = (): never => { throw new Error("报告中的来源证据引用无效或超出安全读取范围，请重新导出。"); };
const knownKeys = (value: ObjectValue, allowed: string[]) => Object.keys(value).every(key => allowed.includes(key));

function checkedProof(value: unknown): Proof {
  if (!object(value) || !knownKeys(value, PROOF_KEYS) || !object(value.measurement_provenance)
    || !knownKeys(value.measurement_provenance, PROVENANCE_KEYS) || !object(value.source_window)
    || !knownKeys(value.source_window, WINDOW_KEYS) || !Array.isArray(value.source_window.source_bindings)
    || value.source_window.source_bindings.some(binding => !object(binding) || !knownKeys(binding, BINDING_KEYS))) return fail();
  const sourceTrack = own(value, "source_track_id") ? value.source_track_id : value.source_window.person_track_id;
  const checked = modelMeasurementEvidenceOf({ ...value, source_track_id: sourceTrack });
  if (!checked) return fail();
  // Canonical key order makes deduplication use complete content equality.
  // Pool IDs are indexes, not weak hashes or a new claim of source authenticity.
  const result: Proof = { measurement_provenance: checked.measurement_provenance, source_window: checked.source_window,
    geometry_mask: Object.fromEntries(Object.entries(checked.geometry_mask).sort(([a], [b]) => a.localeCompare(b))) };
  if (own(value, "source_track_id")) result.source_track_id = checked.source_track_id;
  return result;
}

class Budget {
  nodes = 0; bytes = 0; references = 0;
  add(bytes: number) {
    this.bytes += bytes;
    if (this.bytes > MAX_EXPANDED_BACKUP_BYTES) fail();
  }
  enter(value: unknown, depth: number) {
    if (++this.nodes > MAX_BASE_NODES || depth > MAX_DEPTH) fail();
    if (!value || typeof value !== "object") {
      if (typeof value === "number" && !Number.isFinite(value) || value === undefined || typeof value === "function") fail();
      this.add(byteLength(JSON.stringify(value)) + 1);
    } else this.add(2);
  }
  key(key: string) {
    if (["__proto__", "constructor", "prototype"].includes(key)) fail();
    this.add(byteLength(JSON.stringify(key)) + 2);
  }
  reference(bytes: number) {
    if (++this.references > MAX_MODEL_PROOF_REFERENCES) fail();
    this.add(bytes); // Conservative expanded JSON bound before allocating refs.
  }
}

/** Pack only complete, strictly validated SAM transport proofs. Never mutate input. */
export function packReportBackup(value: unknown, transport: "backup" | "api" = "backup"): unknown {
  if (!object(value)) return value;
  if (own(value, CODEC) || own(value, POOL) || own(value, API_TRANSPORT)) value = unpackReportBackup(value);
  const entries: Array<{ id: number; evidence: Proof }> = [], contentIds = new Map<string, number>();
  const proofCache = new Map<string, { evidence: Proof; cost: number; canonical: string }>();
  const budget = new Budget();
  function visit(raw: unknown, depth: number): unknown {
    budget.enter(raw, depth);
    if (Array.isArray(raw)) return raw.map(child => visit(child, depth + 1));
    if (!object(raw)) return raw;
    if (own(raw, REF) || own(raw, POOL) || own(raw, CODEC) || own(raw, API_TRANSPORT)) return fail();
    let remaining = raw, id: number | undefined;
    if (object(raw.measurement_provenance) && raw.measurement_provenance.pose_source === "sam3d" && raw.source_window && raw.geometry_mask) {
      const candidate = Object.fromEntries(PROOF_KEYS.filter(key => own(raw, key)).map(key => [key, raw[key]]));
      const signature = JSON.stringify(candidate);
      let cached = proofCache.get(signature);
      if (!cached) {
        const evidence = checkedProof(candidate), canonical = JSON.stringify(evidence);
        cached = { evidence, canonical, cost: byteLength(canonical) };
        proofCache.set(signature, cached);
      }
      id = contentIds.get(cached.canonical);
      if (id === undefined) {
        if (entries.length >= MAX_MODEL_PROOFS) return fail();
        id = entries.length; entries.push({ id, evidence: cached.evidence }); contentIds.set(cached.canonical, id);
      }
      budget.reference(cached.cost);
      remaining = Object.fromEntries(Object.entries(raw).filter(([key]) => !PROOF_KEYS.includes(key)));
    }
    const result = Object.fromEntries(Object.entries(remaining).filter(([, child]) => child !== undefined).map(([key, child]) => {
      budget.key(key); return [key, visit(child, depth + 1)];
    }));
    if (id !== undefined) {
      // Match preflight's accounting of the emitted reference key/value.
      budget.key(REF); budget.enter(id, depth + 1);
      result[REF] = id;
    }
    return result;
  }
  const packed = visit(value, 0) as ObjectValue;
  if (!entries.length) return packed;
  if (transport === "backup" && packed.schemaVersion !== "rallymate-practice-report/1") return fail();
  return { ...packed, ...(transport === "api" ? { [API_TRANSPORT]: MODEL_PROOF_CODEC } : {}), [CODEC]: MODEL_PROOF_CODEC, [POOL]: entries };
}

/** Strict preflight, then share frozen proof structures between restored rows. */
export function unpackReportBackup(value: unknown): unknown {
  if (!object(value)) return value;
  const encoded = own(value, CODEC) || own(value, POOL) || own(value, API_TRANSPORT);
  const api = own(value, API_TRANSPORT);
  const proofs: Proof[] = [], costs: number[] = [], content = new Set<string>(), used = new Set<number>();
  if (encoded) {
    if ((api ? value[API_TRANSPORT] !== MODEL_PROOF_CODEC || own(value, "schemaVersion") : value.schemaVersion !== "rallymate-practice-report/1") || value[CODEC] !== MODEL_PROOF_CODEC
      || !Array.isArray(value[POOL]) || !value[POOL].length || value[POOL].length > MAX_MODEL_PROOFS) return fail();
    for (const [index, entry] of value[POOL].entries()) {
      if (!object(entry) || !knownKeys(entry, ["id", "evidence"]) || entry.id !== index) return fail();
      const proof = checkedProof(entry.evidence), signature = JSON.stringify(proof);
      if (content.has(signature)) return fail();
      content.add(signature); proofs.push(proof); costs.push(byteLength(signature));
    }
  }
  const budget = new Budget();
  function preflight(raw: unknown, depth: number) {
    budget.enter(raw, depth);
    if (Array.isArray(raw)) { raw.forEach(child => preflight(child, depth + 1)); return; }
    if (!object(raw)) return;
    if (own(raw, REF)) {
      const id = raw[REF];
      if (!encoded || !Number.isSafeInteger(id) || Number(id) < 0 || Number(id) >= proofs.length
        || PROOF_KEYS.some(key => own(raw, key))) return fail();
      budget.reference(costs[Number(id)]); used.add(Number(id));
    }
    for (const [key, child] of Object.entries(raw)) {
      if (depth === 0 && encoded && [CODEC, POOL, API_TRANSPORT].includes(key)) continue;
      if ([CODEC, POOL, API_TRANSPORT].includes(key)) fail();
      budget.key(key); preflight(child, depth + 1);
    }
  }
  preflight(value, 0);
  if (!encoded) return value; // Existing unpooled backups keep their contract.
  if (used.size !== proofs.length) return fail();
  proofs.forEach(proof => { freezeReportTransport(proof); registerModelTransportEvidence(proof); });
  function expand(raw: unknown): unknown {
    if (Array.isArray(raw)) return raw.map(expand);
    if (!object(raw)) return raw;
    const result = Object.fromEntries(Object.entries(raw).filter(([key]) => ![REF, CODEC, POOL, API_TRANSPORT].includes(key)).map(([key, child]) => [key, expand(child)]));
    return own(raw, REF) ? { ...result, ...proofs[Number(raw[REF])] } : result;
  }
  return freezeReportTransport(expand(value));
}

/** Native API responses retain the pre-existing contract; encoded responses must opt in explicitly. */
export function unpackApiReport(value: unknown): unknown {
  if (!object(value)) return value;
  if ([CODEC, POOL, API_TRANSPORT, REF].some(key => own(value, key))) {
    if (value[API_TRANSPORT] !== MODEL_PROOF_CODEC) return fail();
    return unpackReportBackup(value);
  }
  // A stripped pooled envelope must not become a native response. This scan
  // does not expand or copy evidence and intentionally has a separate budget:
  // legitimate old native SAM reports already exceed the pool's 2M base nodes.
  const active = new WeakSet<object>(), complete = new WeakSet<object>();
  let nodes = 0;
  function scan(raw: unknown, depth: number): void {
    if (++nodes > 50_000_000 || depth > 64) fail();
    if (!raw || typeof raw !== "object") return;
    if (active.has(raw)) fail();
    if (complete.has(raw)) return;
    active.add(raw);
    if (Array.isArray(raw)) for (const child of raw) scan(child, depth + 1);
    else for (const [key, child] of Object.entries(raw)) {
      if ([CODEC, POOL, API_TRANSPORT, REF].includes(key)) fail();
      scan(child, depth + 1);
    }
    active.delete(raw); complete.add(raw);
  }
  scan(value, 0);
  return value;
}
