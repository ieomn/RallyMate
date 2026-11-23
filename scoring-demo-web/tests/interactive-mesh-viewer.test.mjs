import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function compiledUrl(fileUrl) {
  if (fileUrl.pathname.endsWith(".json")) {
    return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  }
  const source = fs.readFileSync(fileUrl, "utf8");
  let js = ts.transpileModule(source, {
    fileName: fileUrl.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl);
    const url = name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name);
    return `from ${JSON.stringify(url)}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}

const meshLib = await import(compiledUrl(new URL("../app/lib/mesh-geometry.ts", import.meta.url)));
const { default: InteractiveMeshViewer, acquireMeshWebGLContext } = await import(
  compiledUrl(new URL("../app/InteractiveMeshViewer.tsx", import.meta.url))
);
const { default: OfficialMeshReplay } = await import(
  compiledUrl(new URL("../app/OfficialMeshReplay.tsx", import.meta.url))
);

const JOB_ID = "7f1c9b2e-4d8a-4e21-9a10-2b3c4d5e6f70";

function encodeInt16Vertices(coords, boundsMin, boundsMax) {
  const buf = new ArrayBuffer(coords.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < coords.length; i++) {
    const axis = i % 3;
    const span = boundsMax[axis] - boundsMin[axis];
    const t = span > 1e-12 ? (coords[i] - boundsMin[axis]) / span : 0.5;
    const q = Math.max(-32767, Math.min(32767, Math.round(t * 65534 - 32767)));
    view.setInt16(i * 2, q, true);
  }
  return Buffer.from(buf).toString("base64");
}

function encodeIndices(values, indexType = "uint16") {
  const stride = indexType === "uint16" ? 2 : 4;
  const buf = new ArrayBuffer(values.length * stride);
  const view = new DataView(buf);
  for (let i = 0; i < values.length; i++) {
    if (indexType === "uint16") view.setUint16(i * 2, values[i], true);
    else view.setUint32(i * 4, values[i], true);
  }
  return Buffer.from(buf).toString("base64");
}

function makeFixturePayload(overrides = {}) {
  // Simple tetrahedron centered at origin with height 1.0
  const vertsFrame0 = [
    0.0, 0.5, 0.0,
    -0.4, -0.5, 0.35,
    0.4, -0.5, 0.35,
    0.0, -0.5, -0.45,
  ];
  const vertsFrame1 = [
    0.05, 0.5, 0.0,
    -0.35, -0.5, 0.35,
    0.45, -0.5, 0.35,
    0.05, -0.5, -0.45,
  ];
  const indices = [
    0, 1, 2,
    0, 2, 3,
    0, 3, 1,
    1, 3, 2,
  ];
  const boundsMin = [-0.4, -0.5, -0.45];
  const boundsMax = [0.45, 0.5, 0.35];
  return {
    version: "sam3d-mesh-geometry-v1",
    job_id: JOB_ID,
    coordinate_system: "y_up_right_handed_centered",
    source_video_sha256: "a".repeat(64),
    source_frame_count: 10,
    selected_frame_count: 2,
    vertex_count: 4,
    face_count: 4,
    original_vertex_count: 4,
    original_face_count: 4,
    decimated: false,
    bounds_min: boundsMin,
    bounds_max: boundsMax,
    normalization: { center: [0, 0, -3], scale: 1.75 },
    material: { base_color_rgb: [166, 189, 219] },
    topology: {
      index_type: "uint16",
      index_count: indices.length,
      indices_b64: encodeIndices(indices, "uint16"),
    },
    quantization: { encoding: "int16_le_b64", range_min: -32767, range_max: 32767 },
    frames: [
      { frame_index: 0, timestamp_ms: 1000, source_timestamp_ms: 1000, person_track_id: 1, vertices_b64: encodeInt16Vertices(vertsFrame0, boundsMin, boundsMax) },
      { frame_index: 5, timestamp_ms: 1200, source_timestamp_ms: 1200, person_track_id: 1, vertices_b64: encodeInt16Vertices(vertsFrame1, boundsMin, boundsMax) },
    ],
    ...overrides,
  };
}

function projectPoint(mat4, [x, y, z]) {
  const cx = mat4[0] * x + mat4[4] * y + mat4[8] * z + mat4[12];
  const cy = mat4[1] * x + mat4[5] * y + mat4[9] * z + mat4[13];
  const cz = mat4[2] * x + mat4[6] * y + mat4[10] * z + mat4[14];
  const cw = mat4[3] * x + mat4[7] * y + mat4[11] * z + mat4[15];
  return [cx / cw, cy / cw, cz / cw, cw];
}

test("validateMeshGeometryPayload enforces strict contract and job isolation", () => {
  const valid = makeFixturePayload();
  const parsed = meshLib.validateMeshGeometryPayload(valid, JOB_ID);
  assert.ok(parsed);
  assert.equal(parsed.job_id, JOB_ID);
  assert.equal(parsed.vertex_count, 4);
  assert.deepEqual(parsed.material.base_color_rgb, [166, 189, 219]);

  // Reject wrong job, version, bounds, index count, uint16 overflow, empty frames, invalid base64.
  assert.equal(meshLib.validateMeshGeometryPayload(valid, "other-job-id"), null);
  assert.equal(meshLib.validateMeshGeometryPayload({ ...valid, version: "legacy-v0" }, JOB_ID), null);
  assert.equal(meshLib.validateMeshGeometryPayload({ ...valid, bounds_min: [1, 0, 0], bounds_max: [0, 1, 1] }, JOB_ID), null);
  assert.equal(meshLib.validateMeshGeometryPayload({ ...valid, topology: { ...valid.topology, index_count: 99 } }, JOB_ID), null);
  assert.equal(meshLib.validateMeshGeometryPayload({ ...valid, vertex_count: 70000 }, JOB_ID), null);
  assert.equal(meshLib.validateMeshGeometryPayload({ ...valid, frames: [] }, JOB_ID), null);
  assert.equal(meshLib.validateMeshGeometryPayload({ ...valid, topology: { ...valid.topology, indices_b64: "not-valid!" } }, JOB_ID), null);
  assert.equal(meshLib.validateMeshGeometryPayload(null, JOB_ID), null);
});

test("decodeInt16Vertices and decodeIndices accurately round-trip uint16 and uint32 buffers", () => {
  const original = [
    -0.4123, 0.4981, -0.2215,
    0.3917, -0.4872, 0.3408,
    0.0112, 0.0044, -0.0089,
  ];
  const boundsMin = [-0.4123, -0.4872, -0.2215];
  const boundsMax = [0.3917, 0.4981, 0.3408];
  const b64 = encodeInt16Vertices(original, boundsMin, boundsMax);
  const decoded = meshLib.decodeInt16Vertices(b64, 3, boundsMin, boundsMax);
  assert.equal(decoded.length, 9);
  for (let i = 0; i < original.length; i++) {
    assert.ok(Math.abs(decoded[i] - original[i]) < 1e-4, `coord ${i} diff too large: ${decoded[i]} vs ${original[i]}`);
  }

  // Zero-span degenerate axis decodes cleanly to exact constant coordinate.
  const flatB64 = encodeInt16Vertices([0, 0.25, 0, 1, 0.25, 0, 0.5, 0.25, 1], [0, 0.25, 0], [1, 0.25, 1]);
  const flatDecoded = meshLib.decodeInt16Vertices(flatB64, 3, [0, 0.25, 0], [1, 0.25, 1]);
  assert.ok(Math.abs(flatDecoded[1] - 0.25) < 1e-6);

  // Uint16 and Uint32 index decoding & bounds protection.
  const u16 = meshLib.decodeIndices(encodeIndices([0, 1, 2], "uint16"), "uint16", 3, 3);
  assert.ok(u16 instanceof Uint16Array);
  assert.deepEqual([...u16], [0, 1, 2]);

  const u32 = meshLib.decodeIndices(encodeIndices([0, 2, 1], "uint32"), "uint32", 3, 3);
  assert.ok(u32 instanceof Uint32Array);
  assert.deepEqual([...u32], [0, 2, 1]);

  assert.throws(() => meshLib.decodeIndices(encodeIndices([0, 1, 9], "uint16"), "uint16", 3, 3), /out of range/);
  assert.throws(() => meshLib.decodeInt16Vertices(b64, 4, boundsMin, boundsMax), /length mismatch/);
});

test("computeVertexNormals and buildWireframeEdgeIndices produce unit normals and deduplicated edges", () => {
  // Single CCW triangle in XY plane facing +Z
  const verts = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const indices = new Uint16Array([0, 1, 2]);
  const normals = meshLib.computeVertexNormals(verts, indices);
  for (let i = 0; i < 3; i++) {
    assert.ok(Math.abs(normals[i * 3]) < 1e-6);
    assert.ok(Math.abs(normals[i * 3 + 1]) < 1e-6);
    assert.ok(Math.abs(normals[i * 3 + 2] - 1) < 1e-6);
  }

  // Degenerate zero-area triangle falls back cleanly to (0, 1, 0) without NaN
  const degenNormals = meshLib.computeVertexNormals(new Float32Array([0, 0, 0, 0, 0, 0, 0, 0, 0]), indices);
  assert.deepEqual([...degenNormals], [0, 1, 0, 0, 1, 0, 0, 1, 0]);

  // Two triangles sharing edge (0, 2) -> 5 unique edges (10 indices), not 6 edges (12 indices)
  const quadEdges = meshLib.buildWireframeEdgeIndices(new Uint16Array([0, 1, 2, 0, 2, 3]), "uint16");
  assert.equal(quadEdges.length, 10);
});

test("orbit camera presets, clamping, projection matrices, floor grid and frame lookup work accurately", () => {
  // Pitch and zoom clamps prevent gimbal lock and clipping
  assert.ok(meshLib.clampOrbitPitch(10) < Math.PI / 2);
  assert.ok(meshLib.clampOrbitPitch(-10) > -Math.PI / 2);
  assert.equal(meshLib.clampOrbitDistance(0.01), meshLib.MIN_ORBIT_DISTANCE);
  assert.equal(meshLib.clampOrbitDistance(99), meshLib.MAX_ORBIT_DISTANCE);

  for (const preset of ["front", "side", "back", "top", "reset"]) {
    const cam = meshLib.orbitPresetState(preset);
    const { viewProjection, eye } = meshLib.orbitViewProjectionMatrix(cam, 16 / 9);
    assert.equal(viewProjection.length, 16);
    assert.ok(viewProjection.every(Number.isFinite));
    // Target (0, 0, 0) must project dead-center to NDC (0, 0) inside [-1, 1] depth
    const [ndcX, ndcY, ndcZ, w] = projectPoint(viewProjection, [0, 0, 0]);
    assert.ok(w > 0);
    assert.ok(Math.abs(ndcX) < 1e-5, `${preset} ndcX=${ndcX}`);
    assert.ok(Math.abs(ndcY) < 1e-5, `${preset} ndcY=${ndcY}`);
    assert.ok(ndcZ > -1 && ndcZ < 1, `${preset} ndcZ=${ndcZ}`);
    if (preset === "front") assert.ok(eye[2] > 0 && Math.abs(eye[0]) < 1e-6);
    if (preset === "side") assert.ok(eye[0] > 0 && Math.abs(eye[2]) < 1e-5);
    if (preset === "back") assert.ok(eye[2] < 0 && Math.abs(eye[0]) < 1e-5);
    if (preset === "top") assert.ok(eye[1] > 2.0);
  }

  const frames = [{ timestampMs: 1000 }, { timestampMs: 1500 }, { timestampMs: 2000 }];
  assert.equal(meshLib.findNearestFrameIndex(frames, 900), 0);
  assert.equal(meshLib.findNearestFrameIndex(frames, 1240), 0);
  assert.equal(meshLib.findNearestFrameIndex(frames, 1260), 1);
  assert.equal(meshLib.findNearestFrameIndex(frames, 2400), 2);

  const grid = meshLib.buildCourtFloorGrid(-0.5, 1.5, 6);
  assert.equal(grid.length, (6 + 1) * 2 * 2 * 3);
  for (let i = 1; i < grid.length; i += 3) {
    assert.equal(grid[i], -0.5);
  }
});

test("OfficialMeshReplay keeps overlay default and adds 3D free-view toggle safely under SSR", () => {
  const media = {
    status: "ready",
    video_url: `/v1/jobs/${JOB_ID}/artifacts/sam3d-mesh.mp4`,
    side_video_url: `/v1/jobs/${JOB_ID}/artifacts/sam3d-mesh-side.mp4`,
    preview_image_url: `/v1/jobs/${JOB_ID}/artifacts/sam3d-mesh-preview.jpg`,
    manifest_url: `/v1/jobs/${JOB_ID}/artifacts/sam3d-mesh-manifest.json`,
    timing_preserved: true,
    source_video_sha256: "a".repeat(64),
    source_timestamp_origin_ms: 18000,
    source_relative_origin_ms: 18000,
    frame_count: 90,
    renderer: "official-sam3d-pyrender-persistent-context",
  };
  const html = renderToStaticMarkup(createElement(OfficialMeshReplay, { media, jobId: JOB_ID }));
  assert.match(html, /aria-pressed="true"[^>]*>原片叠加<\/button>/);
  assert.match(html, /aria-pressed="false"[^>]*>侧面 90°<\/button>/);
  assert.match(html, /aria-pressed="false"[^>]*>3D 自由视角<\/button>/);
  assert.match(html, /<video[^>]*data-replay-role="mesh"/);
});

test("InteractiveMeshViewer renders safely under SSR for loading, ready, and non-WebGL fallback states", () => {
  // 1. Loading state (no preloaded payload)
  const loadingHtml = renderToStaticMarkup(createElement(InteractiveMeshViewer, { jobId: JOB_ID }));
  assert.match(loadingHtml, /data-mesh-viewer-state="loading"/);
  assert.match(loadingHtml, /正在读取 3D 人体网格几何数据/);

  // 2. Ready state with validated payload renders presets, wireframe, speeds, scrubber and canvas
  const readyHtml = renderToStaticMarkup(
    createElement(InteractiveMeshViewer, { jobId: JOB_ID, initialPayload: makeFixturePayload() })
  );
  assert.match(readyHtml, /data-mesh-viewer-state="ready"/);
  assert.match(readyHtml, /<canvas/);
  for (const label of ["正面", "侧面 90°", "背面", "俯视", "重置", "线框", "暂停", "0.25×", "0.5×", "1×"]) {
    assert.ok(readyHtml.includes(label), `missing control label: ${label}`);
  }
  assert.match(readyHtml, /type="range"/);
  assert.match(readyHtml, /1\/2/);

  // 3. Non-WebGL graceful fallback UI and context acquisition guard
  const fallbackHtml = renderToStaticMarkup(
    createElement(InteractiveMeshViewer, {
      jobId: JOB_ID,
      initialPayload: makeFixturePayload(),
      webglSupported: false,
      onFallbackToVideo: () => {},
    })
  );
  assert.match(fallbackHtml, /data-mesh-viewer-state="webgl-unsupported"/);
  assert.match(fallbackHtml, /不支持 WebGL/);
  assert.match(fallbackHtml, /返回原片叠加视频/);
  assert.doesNotMatch(fallbackHtml, /<canvas/);

  // Direct WebGL context acquisition fallback checks
  assert.equal(acquireMeshWebGLContext({ getContext: () => null }, "uint16"), null);
  assert.equal(
    acquireMeshWebGLContext(
      { getContext: id => (id === "webgl" ? { getExtension: () => null } : null) },
      "uint32",
    ),
    null,
  );
  const mockGl1 = { getExtension: name => (name === "OES_element_index_uint" ? {} : null) };
  assert.equal(
    acquireMeshWebGLContext({ getContext: id => (id === "webgl" ? mockGl1 : null) }, "uint32"),
    mockGl1,
  );
});
