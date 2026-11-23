export const MESH_GEOMETRY_VERSION = "sam3d-mesh-geometry-v1" as const;
export const OFFICIAL_MESH_LIGHT_BLUE_RGB: readonly [number, number, number] = [166, 189, 219];

const QUANTIZATION_MIN = -32767;
const QUANTIZATION_MAX = 32767;
const QUANTIZATION_SPAN = QUANTIZATION_MAX - QUANTIZATION_MIN; // 65534
const MAX_VERTEX_COUNT = 100000;
const MAX_FACE_COUNT = 200000;
const MAX_FRAME_COUNT = 120;
const BASE64_PATTERN = /^[A-Za-z0-9+/]*={0,2}$/;

export type MeshGeometryIndexType = "uint16" | "uint32";

export type MeshGeometryTopology = {
  index_type: MeshGeometryIndexType;
  index_count: number;
  indices_b64: string;
};

export type MeshGeometryFrame = {
  frame_index: number;
  timestamp_ms: number;
  source_timestamp_ms: number | null;
  person_track_id: number | null;
  vertices_b64: string;
};

export type MeshGeometryPayload = {
  version: typeof MESH_GEOMETRY_VERSION;
  job_id: string;
  coordinate_system: string;
  source_video_sha256: string | null;
  source_frame_count: number;
  selected_frame_count: number;
  vertex_count: number;
  face_count: number;
  original_vertex_count: number;
  original_face_count: number;
  decimated: boolean;
  bounds_min: [number, number, number];
  bounds_max: [number, number, number];
  normalization: {
    center: [number, number, number];
    scale: number;
  };
  material: {
    base_color_rgb: [number, number, number];
  };
  topology: MeshGeometryTopology;
  quantization: {
    encoding: "int16_le_b64";
    range_min: number;
    range_max: number;
  };
  frames: MeshGeometryFrame[];
};

export type DecodedMeshFrame = {
  frameIndex: number;
  timestampMs: number;
  sourceTimestampMs: number | null;
  personTrackId: number | null;
  vertices: Float32Array;
};

export type DecodedMeshClip = {
  jobId: string;
  vertexCount: number;
  faceCount: number;
  sourceFrameCount: number;
  decimated: boolean;
  boundsMin: [number, number, number];
  boundsMax: [number, number, number];
  baseColorRgb: [number, number, number];
  indexType: MeshGeometryIndexType;
  indices: Uint16Array | Uint32Array;
  frames: DecodedMeshFrame[];
};

export type OrbitViewPreset = "front" | "side" | "back" | "top" | "reset";

export type OrbitCameraState = {
  yaw: number;
  pitch: number;
  distance: number;
  target: [number, number, number];
  fovRad?: number;
  near?: number;
  far?: number;
};

export type OrbitMatrices = {
  view: Float32Array;
  projection: Float32Array;
  viewProjection: Float32Array;
  eye: [number, number, number];
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseVec3(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 3) return null;
  const [x, y, z] = value;
  if (!isFiniteNumber(x) || !isFiniteNumber(y) || !isFiniteNumber(z)) return null;
  return [x, y, z];
}

function isBase64String(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length % 4 === 0 && BASE64_PATTERN.test(value);
}

export function validateMeshGeometryPayload(value: unknown, expectedJobId?: string): MeshGeometryPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (data.version !== MESH_GEOMETRY_VERSION) return null;
  if (typeof data.job_id !== "string" || !data.job_id) return null;
  if (expectedJobId && data.job_id !== expectedJobId) return null;

  const vertexCount = data.vertex_count;
  const faceCount = data.face_count;
  if (!Number.isSafeInteger(vertexCount) || (vertexCount as number) < 3 || (vertexCount as number) > MAX_VERTEX_COUNT) return null;
  if (!Number.isSafeInteger(faceCount) || (faceCount as number) < 1 || (faceCount as number) > MAX_FACE_COUNT) return null;

  const boundsMin = parseVec3(data.bounds_min);
  const boundsMax = parseVec3(data.bounds_max);
  if (!boundsMin || !boundsMax) return null;
  for (let i = 0; i < 3; i++) {
    if (boundsMin[i] > boundsMax[i]) return null;
  }

  const quant = data.quantization;
  if (!quant || typeof quant !== "object" || Array.isArray(quant)) return null;
  const quantObj = quant as Record<string, unknown>;
  if (quantObj.encoding !== "int16_le_b64" || quantObj.range_min !== QUANTIZATION_MIN || quantObj.range_max !== QUANTIZATION_MAX) {
    return null;
  }

  const topo = data.topology;
  if (!topo || typeof topo !== "object" || Array.isArray(topo)) return null;
  const topoObj = topo as Record<string, unknown>;
  if (topoObj.index_type !== "uint16" && topoObj.index_type !== "uint32") return null;
  if (topoObj.index_type === "uint16" && (vertexCount as number) > 65535) return null;
  if (topoObj.index_count !== (faceCount as number) * 3) return null;
  if (!isBase64String(topoObj.indices_b64)) return null;

  if (!Array.isArray(data.frames) || data.frames.length < 1 || data.frames.length > MAX_FRAME_COUNT) return null;
  const frames: MeshGeometryFrame[] = [];
  for (const item of data.frames) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const row = item as Record<string, unknown>;
    if (!Number.isSafeInteger(row.frame_index) || (row.frame_index as number) < 0) return null;
    if (!isFiniteNumber(row.timestamp_ms)) return null;
    if (row.source_timestamp_ms !== undefined && row.source_timestamp_ms !== null && !isFiniteNumber(row.source_timestamp_ms)) {
      return null;
    }
    if (row.person_track_id !== undefined && row.person_track_id !== null && !Number.isSafeInteger(row.person_track_id)) {
      return null;
    }
    if (!isBase64String(row.vertices_b64)) return null;
    frames.push({
      frame_index: row.frame_index as number,
      timestamp_ms: row.timestamp_ms,
      source_timestamp_ms: isFiniteNumber(row.source_timestamp_ms) ? row.source_timestamp_ms : null,
      person_track_id: Number.isSafeInteger(row.person_track_id) ? (row.person_track_id as number) : null,
      vertices_b64: row.vertices_b64,
    });
  }

  const rawNorm = data.normalization && typeof data.normalization === "object" && !Array.isArray(data.normalization)
    ? (data.normalization as Record<string, unknown>)
    : null;
  const normCenter = parseVec3(rawNorm?.center) ?? [0, 0, 0];
  const normScale = isFiniteNumber(rawNorm?.scale) && rawNorm.scale > 0 ? rawNorm.scale : 1;

  const rawMat = data.material && typeof data.material === "object" && !Array.isArray(data.material)
    ? (data.material as Record<string, unknown>)
    : null;
  const parsedColor = parseVec3(rawMat?.base_color_rgb);
  const baseColorRgb: [number, number, number] = parsedColor
    && parsedColor.every(c => c >= 0 && c <= 255)
    ? parsedColor
    : [OFFICIAL_MESH_LIGHT_BLUE_RGB[0], OFFICIAL_MESH_LIGHT_BLUE_RGB[1], OFFICIAL_MESH_LIGHT_BLUE_RGB[2]];

  const sourceVideoSha = typeof data.source_video_sha256 === "string" && /^[0-9a-f]{64}$/i.test(data.source_video_sha256)
    ? data.source_video_sha256.toLowerCase()
    : null;

  return {
    version: MESH_GEOMETRY_VERSION,
    job_id: data.job_id,
    coordinate_system: typeof data.coordinate_system === "string" ? data.coordinate_system : "y_up_right_handed_centered",
    source_video_sha256: sourceVideoSha,
    source_frame_count: Number.isSafeInteger(data.source_frame_count) && (data.source_frame_count as number) >= frames.length
      ? (data.source_frame_count as number)
      : frames.length,
    selected_frame_count: frames.length,
    vertex_count: vertexCount as number,
    face_count: faceCount as number,
    original_vertex_count: Number.isSafeInteger(data.original_vertex_count) ? (data.original_vertex_count as number) : (vertexCount as number),
    original_face_count: Number.isSafeInteger(data.original_face_count) ? (data.original_face_count as number) : (faceCount as number),
    decimated: Boolean(data.decimated),
    bounds_min: boundsMin,
    bounds_max: boundsMax,
    normalization: { center: normCenter, scale: normScale },
    material: { base_color_rgb: baseColorRgb },
    topology: {
      index_type: topoObj.index_type,
      index_count: topoObj.index_count,
      indices_b64: topoObj.indices_b64,
    },
    quantization: {
      encoding: "int16_le_b64",
      range_min: QUANTIZATION_MIN,
      range_max: QUANTIZATION_MAX,
    },
    frames,
  };
}

export function decodeBase64Bytes(b64: string): Uint8Array {
  if (!isBase64String(b64)) {
    throw new Error("Invalid base64 mesh buffer");
  }
  if (typeof atob === "function") {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }
  const buf = Buffer.from(b64, "base64");
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
}

export function decodeInt16Vertices(
  b64: string,
  vertexCount: number,
  boundsMin: readonly [number, number, number],
  boundsMax: readonly [number, number, number],
): Float32Array {
  const bytes = decodeBase64Bytes(b64);
  const expectedBytes = vertexCount * 3 * 2;
  if (bytes.byteLength !== expectedBytes) {
    throw new Error(`Vertex buffer length mismatch: expected ${expectedBytes} bytes, got ${bytes.byteLength}`);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(vertexCount * 3);
  const minX = boundsMin[0];
  const minY = boundsMin[1];
  const minZ = boundsMin[2];
  const scaleX = (boundsMax[0] - minX) / QUANTIZATION_SPAN;
  const scaleY = (boundsMax[1] - minY) / QUANTIZATION_SPAN;
  const scaleZ = (boundsMax[2] - minZ) / QUANTIZATION_SPAN;

  for (let i = 0; i < vertexCount; i++) {
    const byteOffset = i * 6;
    const outOffset = i * 3;
    out[outOffset] = minX + (view.getInt16(byteOffset, true) - QUANTIZATION_MIN) * scaleX;
    out[outOffset + 1] = minY + (view.getInt16(byteOffset + 2, true) - QUANTIZATION_MIN) * scaleY;
    out[outOffset + 2] = minZ + (view.getInt16(byteOffset + 4, true) - QUANTIZATION_MIN) * scaleZ;
  }
  return out;
}

export function decodeIndices(
  b64: string,
  indexType: MeshGeometryIndexType,
  expectedCount: number,
  vertexCount: number,
): Uint16Array | Uint32Array {
  const bytes = decodeBase64Bytes(b64);
  const stride = indexType === "uint16" ? 2 : 4;
  const expectedBytes = expectedCount * stride;
  if (bytes.byteLength !== expectedBytes) {
    throw new Error(`Index buffer length mismatch: expected ${expectedBytes} bytes, got ${bytes.byteLength}`);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (indexType === "uint16") {
    const out = new Uint16Array(expectedCount);
    for (let i = 0; i < expectedCount; i++) {
      const idx = view.getUint16(i * 2, true);
      if (idx >= vertexCount) {
        throw new Error(`Mesh triangle index ${idx} out of range for ${vertexCount} vertices`);
      }
      out[i] = idx;
    }
    return out;
  }
  const out = new Uint32Array(expectedCount);
  for (let i = 0; i < expectedCount; i++) {
    const idx = view.getUint32(i * 4, true);
    if (idx >= vertexCount) {
      throw new Error(`Mesh triangle index ${idx} out of range for ${vertexCount} vertices`);
    }
    out[i] = idx;
  }
  return out;
}

export function decodeMeshGeometryPayload(payload: MeshGeometryPayload): DecodedMeshClip {
  const indices = decodeIndices(
    payload.topology.indices_b64,
    payload.topology.index_type,
    payload.topology.index_count,
    payload.vertex_count,
  );
  const frames: DecodedMeshFrame[] = payload.frames.map(frame => ({
    frameIndex: frame.frame_index,
    timestampMs: frame.timestamp_ms,
    sourceTimestampMs: frame.source_timestamp_ms,
    personTrackId: frame.person_track_id,
    vertices: decodeInt16Vertices(
      frame.vertices_b64,
      payload.vertex_count,
      payload.bounds_min,
      payload.bounds_max,
    ),
  }));
  return {
    jobId: payload.job_id,
    vertexCount: payload.vertex_count,
    faceCount: payload.face_count,
    sourceFrameCount: payload.source_frame_count,
    decimated: payload.decimated,
    boundsMin: payload.bounds_min,
    boundsMax: payload.bounds_max,
    baseColorRgb: payload.material.base_color_rgb,
    indexType: payload.topology.index_type,
    indices,
    frames,
  };
}

/** Area-weighted smooth per-vertex normals for Gouraud/Phong WebGL shading. */
export function computeVertexNormals(
  vertices: Float32Array,
  indices: Uint16Array | Uint32Array,
  out?: Float32Array,
): Float32Array {
  const normals = out && out.length === vertices.length ? out : new Float32Array(vertices.length);
  normals.fill(0);

  for (let i = 0; i + 2 < indices.length; i += 3) {
    const i0 = indices[i] * 3;
    const i1 = indices[i + 1] * 3;
    const i2 = indices[i + 2] * 3;

    const x0 = vertices[i0];
    const y0 = vertices[i0 + 1];
    const z0 = vertices[i0 + 2];

    const e1x = vertices[i1] - x0;
    const e1y = vertices[i1 + 1] - y0;
    const e1z = vertices[i1 + 2] - z0;

    const e2x = vertices[i2] - x0;
    const e2y = vertices[i2 + 1] - y0;
    const e2z = vertices[i2 + 2] - z0;

    const nx = e1y * e2z - e1z * e2y;
    const ny = e1z * e2x - e1x * e2z;
    const nz = e1x * e2y - e1y * e2x;

    normals[i0] += nx;
    normals[i0 + 1] += ny;
    normals[i0 + 2] += nz;

    normals[i1] += nx;
    normals[i1 + 1] += ny;
    normals[i1 + 2] += nz;

    normals[i2] += nx;
    normals[i2 + 1] += ny;
    normals[i2 + 2] += nz;
  }

  for (let i = 0; i < normals.length; i += 3) {
    const nx = normals[i];
    const ny = normals[i + 1];
    const nz = normals[i + 2];
    const len = Math.hypot(nx, ny, nz);
    if (len > 1e-10) {
      const inv = 1 / len;
      normals[i] = nx * inv;
      normals[i + 1] = ny * inv;
      normals[i + 2] = nz * inv;
    } else {
      normals[i] = 0;
      normals[i + 1] = 1;
      normals[i + 2] = 0;
    }
  }
  return normals;
}

/** Build deduplicated triangle edge indices for crisp WebGL wireframe rendering. */
export function buildWireframeEdgeIndices(
  indices: Uint16Array | Uint32Array,
  indexType: MeshGeometryIndexType = indices instanceof Uint32Array ? "uint32" : "uint16",
): Uint16Array | Uint32Array {
  const seen = new Set<number>();
  const edges: number[] = [];
  const pushEdge = (a: number, b: number) => {
    const lo = a < b ? a : b;
    const hi = a < b ? b : a;
    // MAX_VERTEX_COUNT is 100000 (< 2^17), so lo * 131072 + hi stays safely inside 2^53.
    const key = lo * 131072 + hi;
    if (!seen.has(key)) {
      seen.add(key);
      edges.push(lo, hi);
    }
  };
  for (let i = 0; i + 2 < indices.length; i += 3) {
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];
    pushEdge(a, b);
    pushEdge(b, c);
    pushEdge(c, a);
  }
  return indexType === "uint16" ? new Uint16Array(edges) : new Uint32Array(edges);
}

export const MIN_ORBIT_PITCH = -Math.PI / 2 + 0.08;
export const MAX_ORBIT_PITCH = Math.PI / 2 - 0.08;
export const MIN_ORBIT_DISTANCE = 0.65;
export const MAX_ORBIT_DISTANCE = 6.0;

export function clampOrbitPitch(pitch: number): number {
  if (!Number.isFinite(pitch)) return 0.14;
  return Math.max(MIN_ORBIT_PITCH, Math.min(MAX_ORBIT_PITCH, pitch));
}

export function clampOrbitDistance(distance: number): number {
  if (!Number.isFinite(distance)) return 2.35;
  return Math.max(MIN_ORBIT_DISTANCE, Math.min(MAX_ORBIT_DISTANCE, distance));
}

export function orbitPresetState(
  preset: OrbitViewPreset,
  target: readonly [number, number, number] = [0, 0, 0],
): OrbitCameraState {
  const center: [number, number, number] = [target[0], target[1], target[2]];
  switch (preset) {
    case "side":
      return { yaw: Math.PI / 2, pitch: 0.1, distance: 2.35, target: center };
    case "back":
      return { yaw: Math.PI, pitch: 0.14, distance: 2.35, target: center };
    case "top":
      return { yaw: 0, pitch: 1.32, distance: 2.55, target: center };
    case "front":
    case "reset":
    default:
      return { yaw: 0, pitch: 0.14, distance: 2.35, target: center };
  }
}

export function orbitCameraPosition(state: OrbitCameraState): [number, number, number] {
  const pitch = clampOrbitPitch(state.pitch);
  const distance = clampOrbitDistance(state.distance);
  const cosPitch = Math.cos(pitch);
  return [
    state.target[0] + distance * cosPitch * Math.sin(state.yaw),
    state.target[1] + distance * Math.sin(pitch),
    state.target[2] + distance * cosPitch * Math.cos(state.yaw),
  ];
}

export function perspectiveMatrix(fovRad: number, aspect: number, near: number, far: number): Float32Array {
  const safeAspect = Number.isFinite(aspect) && aspect > 0.05 ? aspect : 1;
  const f = 1 / Math.tan(fovRad / 2);
  const nf = 1 / (near - far);
  const out = new Float32Array(16);
  out[0] = f / safeAspect;
  out[5] = f;
  out[10] = (far + near) * nf;
  out[11] = -1;
  out[14] = 2 * far * near * nf;
  return out;
}

export function lookAtMatrix(
  eye: readonly [number, number, number],
  target: readonly [number, number, number],
  up: readonly [number, number, number] = [0, 1, 0],
): Float32Array {
  let zx = eye[0] - target[0];
  let zy = eye[1] - target[1];
  let zz = eye[2] - target[2];
  let zLen = Math.hypot(zx, zy, zz);
  if (zLen < 1e-8) {
    zx = 0;
    zy = 0;
    zz = 1;
    zLen = 1;
  }
  zx /= zLen;
  zy /= zLen;
  zz /= zLen;

  let xx = up[1] * zz - up[2] * zy;
  let xy = up[2] * zx - up[0] * zz;
  let xz = up[0] * zy - up[1] * zx;
  const xLen = Math.hypot(xx, xy, xz);
  if (xLen < 1e-8) {
    xx = 1;
    xy = 0;
    xz = 0;
  } else {
    xx /= xLen;
    xy /= xLen;
    xz /= xLen;
  }

  const yx = zy * xz - zz * xy;
  const yy = zz * xx - zx * xz;
  const yz = zx * xy - zy * xx;

  const out = new Float32Array(16);
  out[0] = xx;
  out[1] = yx;
  out[2] = zx;
  out[3] = 0;

  out[4] = xy;
  out[5] = yy;
  out[6] = zy;
  out[7] = 0;

  out[8] = xz;
  out[9] = yz;
  out[10] = zz;
  out[11] = 0;

  out[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
  out[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
  out[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
  out[15] = 1;
  return out;
}

export function multiplyMat4(a: Float32Array, b: Float32Array): Float32Array {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++) {
    const cOffset = col * 4;
    const b0 = b[cOffset];
    const b1 = b[cOffset + 1];
    const b2 = b[cOffset + 2];
    const b3 = b[cOffset + 3];
    for (let row = 0; row < 4; row++) {
      out[cOffset + row] =
        a[row] * b0
        + a[4 + row] * b1
        + a[8 + row] * b2
        + a[12 + row] * b3;
    }
  }
  return out;
}

export function orbitViewProjectionMatrix(state: OrbitCameraState, aspect: number): OrbitMatrices {
  const eye = orbitCameraPosition(state);
  const view = lookAtMatrix(eye, state.target);
  const projection = perspectiveMatrix(
    state.fovRad ?? (Math.PI * 38) / 180,
    aspect,
    state.near ?? 0.05,
    state.far ?? 50,
  );
  const viewProjection = multiplyMat4(projection, view);
  return { view, projection, viewProjection, eye };
}

export function findNearestFrameIndex(frames: readonly { timestampMs: number }[], targetMs: number): number {
  if (frames.length <= 1 || !Number.isFinite(targetMs)) return 0;
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].timestampMs < targetMs) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(frames[lo - 1].timestampMs - targetMs) <= Math.abs(frames[lo].timestampMs - targetMs)) {
    return lo - 1;
  }
  return lo;
}

/** Generate line-segment vertices for a subtle night-court floor grid at y = floorY. */
export function buildCourtFloorGrid(floorY: number, halfSize = 1.6, divisions = 12): Float32Array {
  const steps = Math.max(2, Math.floor(divisions));
  const linesPerAxis = steps + 1;
  const vertices = new Float32Array(linesPerAxis * 2 * 2 * 3);
  let offset = 0;
  for (let i = 0; i <= steps; i++) {
    const t = -halfSize + (2 * halfSize * i) / steps;
    // Line parallel to Z
    vertices[offset++] = t;
    vertices[offset++] = floorY;
    vertices[offset++] = -halfSize;
    vertices[offset++] = t;
    vertices[offset++] = floorY;
    vertices[offset++] = halfSize;
    // Line parallel to X
    vertices[offset++] = -halfSize;
    vertices[offset++] = floorY;
    vertices[offset++] = t;
    vertices[offset++] = halfSize;
    vertices[offset++] = floorY;
    vertices[offset++] = t;
  }
  return vertices;
}
