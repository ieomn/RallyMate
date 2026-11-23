"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { accountFetch } from "./lib/auth-client";
import {
  buildCourtFloorGrid,
  buildWireframeEdgeIndices,
  clampOrbitDistance,
  clampOrbitPitch,
  computeVertexNormals,
  decodeMeshGeometryPayload,
  findNearestFrameIndex,
  orbitPresetState,
  orbitViewProjectionMatrix,
  validateMeshGeometryPayload,
  type DecodedMeshClip,
  type MeshGeometryPayload,
  type OrbitCameraState,
  type OrbitViewPreset,
} from "./lib/mesh-geometry";

export type InteractiveMeshViewerProps = {
  jobId: string;
  startMs?: number;
  endMs?: number;
  initialTimeMs?: number;
  initialPayload?: MeshGeometryPayload | null;
  webglSupported?: boolean;
  onFallbackToVideo?: () => void;
};

type WebGLCanvasLike = {
  getContext: (contextId: string, options?: Record<string, unknown>) => unknown;
};

/** Acquire a WebGL2 or WebGL1 context and verify uint32 index support when needed. */
export function acquireMeshWebGLContext(
  canvas: WebGLCanvasLike | null | undefined,
  indexType: "uint16" | "uint32",
): WebGLRenderingContext | null {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  const options = { antialias: true, alpha: false, preserveDrawingBuffer: false };
  const gl2 = canvas.getContext("webgl2", options) as WebGLRenderingContext | null;
  if (gl2) return gl2;
  const gl1 = canvas.getContext("webgl", options) as WebGLRenderingContext | null;
  if (!gl1) return null;
  if (indexType === "uint32" && !gl1.getExtension("OES_element_index_uint")) {
    return null;
  }
  return gl1;
}

type FetchResultState =
  | { key: string; status: "ready"; clip: DecodedMeshClip; errorMessage: null }
  | { key: string; status: "unavailable" | "error"; clip: null; errorMessage: string };

type GlResources = {
  gl: WebGLRenderingContext;
  meshProgram: WebGLProgram;
  lineProgram: WebGLProgram;
  meshLoc: {
    aPosition: number;
    aNormal: number;
    uViewProjection: WebGLUniformLocation | null;
    uBaseColor: WebGLUniformLocation | null;
    uCameraPos: WebGLUniformLocation | null;
    uWireframeMode: WebGLUniformLocation | null;
  };
  lineLoc: {
    aPosition: number;
    uViewProjection: WebGLUniformLocation | null;
    uLineColor: WebGLUniformLocation | null;
    uFadeRadius: WebGLUniformLocation | null;
  };
  posBuffer: WebGLBuffer;
  normBuffer: WebGLBuffer;
  triIndexBuffer: WebGLBuffer;
  wireIndexBuffer: WebGLBuffer;
  gridBuffer: WebGLBuffer;
  triCount: number;
  wireCount: number;
  gridVertexCount: number;
  glIndexType: number;
  normalScratch: Float32Array;
  uploadedFrameIndex: number;
};

const PLAYBACK_SPEEDS = [0.25, 0.5, 1] as const;
const VIEW_PRESETS: readonly { id: OrbitViewPreset; label: string }[] = [
  { id: "front", label: "正面" },
  { id: "side", label: "侧面 90°" },
  { id: "back", label: "背面" },
  { id: "top", label: "俯视" },
  { id: "reset", label: "重置" },
];

const MESH_VERTEX_SHADER = `
attribute vec3 aPosition;
attribute vec3 aNormal;
uniform mat4 uViewProjection;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
void main() {
  vWorldPos = aPosition;
  vWorldNormal = aNormal;
  gl_Position = uViewProjection * vec4(aPosition, 1.0);
}
`;

const MESH_FRAGMENT_SHADER = `
precision mediump float;
uniform vec3 uBaseColor;
uniform vec3 uCameraPos;
uniform float uWireframeMode;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
void main() {
  vec3 n = normalize(vWorldNormal);
  vec3 v = normalize(uCameraPos - vWorldPos);
  if (!gl_FrontFacing) {
    n = -n;
  }
  vec3 keyDir = normalize(vec3(0.55, 0.72, 0.62));
  vec3 fillDir = normalize(vec3(-0.68, 0.25, -0.45));
  float keyDiff = max(dot(n, keyDir), 0.0);
  float fillDiff = max(dot(n, fillDir), 0.0);
  vec3 keyHalf = normalize(keyDir + v);
  float spec = pow(max(dot(n, keyHalf), 0.0), 28.0) * 0.22;
  float hemi = 0.5 + 0.5 * n.y;
  vec3 ambient = mix(vec3(0.11, 0.16, 0.20), vec3(0.28, 0.34, 0.42), hemi);
  float rim = pow(1.0 - max(dot(n, v), 0.0), 2.6) * 0.28;
  vec3 lit = uBaseColor * (ambient + keyDiff * 0.74 + fillDiff * 0.30)
           + vec3(0.88, 0.94, 1.0) * spec
           + vec3(0.62, 0.80, 0.98) * rim;
  lit = mix(lit, lit * 0.64, uWireframeMode);
  gl_FragColor = vec4(clamp(lit, 0.0, 1.0), 1.0);
}
`;

const LINE_VERTEX_SHADER = `
attribute vec3 aPosition;
uniform mat4 uViewProjection;
varying vec3 vWorldPos;
void main() {
  vWorldPos = aPosition;
  gl_Position = uViewProjection * vec4(aPosition, 1.0);
}
`;

const LINE_FRAGMENT_SHADER = `
precision mediump float;
uniform vec4 uLineColor;
uniform float uFadeRadius;
varying vec3 vWorldPos;
void main() {
  float alpha = uLineColor.a;
  if (uFadeRadius > 0.0) {
    float dist = length(vWorldPos.xz);
    alpha *= clamp(1.0 - smoothstep(uFadeRadius * 0.35, uFadeRadius, dist), 0.0, 1.0);
  }
  gl_FragColor = vec4(uLineColor.rgb, alpha);
}
`;

function compileShader(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function linkProgram(gl: WebGLRenderingContext, vertexSource: string, fragmentSource: string): WebGLProgram | null {
  const vs = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  if (!vs || !fs) {
    if (vs) gl.deleteShader(vs);
    if (fs) gl.deleteShader(fs);
    return null;
  }
  const program = gl.createProgram();
  if (!program) {
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    return null;
  }
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return null;
  }
  return program;
}

function formatMeshTimeMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "00:00.0";
  const totalTenths = Math.round(ms / 100);
  const tenths = totalTenths % 10;
  const totalSeconds = Math.floor(totalTenths / 10);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

function buildGeometryUrl(jobId: string, startMs?: number, endMs?: number): string {
  const params = new URLSearchParams({ max_frames: "60", max_faces: "12000" });
  if (typeof startMs === "number" && Number.isFinite(startMs) && startMs >= 0) {
    params.set("start_ms", String(Math.floor(startMs)));
  }
  if (typeof endMs === "number" && Number.isFinite(endMs) && endMs >= 0) {
    params.set("end_ms", String(Math.ceil(endMs)));
  }
  return `/v1/jobs/${encodeURIComponent(jobId)}/mesh-geometry?${params.toString()}`;
}

export default function InteractiveMeshViewer({
  jobId,
  startMs,
  endMs,
  initialTimeMs,
  initialPayload = null,
  webglSupported = true,
  onFallbackToVideo,
}: InteractiveMeshViewerProps) {
  const [attempt, setAttempt] = useState(0);
  const [fetched, setFetched] = useState<FetchResultState | null>(null);
  const [webglFailedKey, setWebglFailedKey] = useState<string | null>(null);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState<(typeof PLAYBACK_SPEEDS)[number]>(0.5);
  const [wireframe, setWireframe] = useState(false);
  const [activePreset, setActivePreset] = useState<OrbitViewPreset | null>("front");
  const [frameState, setFrameState] = useState<{ clipKey: string; index: number }>({ clipKey: "", index: 0 });
  const [dragging, setDragging] = useState(false);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const glRef = useRef<GlResources | null>(null);
  const cameraRef = useRef<OrbitCameraState>(orbitPresetState("front"));
  const wireframeRef = useRef(false);
  const playingRef = useRef(true);
  const speedRef = useRef<number>(0.5);
  const frameIndexRef = useRef(0);
  const clipClockMsRef = useRef(0);
  const pointersRef = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinchDistanceRef = useRef<number | null>(null);

  const requestKey = `${jobId}:${startMs ?? ""}:${endMs ?? ""}:${attempt}`;

  const preloadedClip = useMemo<DecodedMeshClip | null>(() => {
    if (!initialPayload) return null;
    const validated = validateMeshGeometryPayload(initialPayload, jobId);
    if (!validated) return null;
    try {
      return decodeMeshGeometryPayload(validated);
    } catch {
      return null;
    }
  }, [initialPayload, jobId]);

  useEffect(() => {
    if (preloadedClip) return;
    const controller = new AbortController();
    const url = buildGeometryUrl(jobId, startMs, endMs);

    void (async () => {
      try {
        const response = await accountFetch(url, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (response.status === 404 || response.status === 409) {
          setFetched({
            key: requestKey,
            status: "unavailable",
            clip: null,
            errorMessage: response.status === 409
              ? "分析任务尚未完成，暂无法加载 3D 交互网格。"
              : "当前任务未保存可交互 3D 网格几何数据。",
          });
          return;
        }
        if (!response.ok) {
          setFetched({
            key: requestKey,
            status: "error",
            clip: null,
            errorMessage: "读取 3D 网格几何数据时发生网络或服务错误。",
          });
          return;
        }
        const rawJson: unknown = await response.json().catch(() => null);
        if (controller.signal.aborted) return;
        const validated = validateMeshGeometryPayload(rawJson, jobId);
        if (!validated) {
          setFetched({
            key: requestKey,
            status: "error",
            clip: null,
            errorMessage: "3D 网格数据格式校验未通过。",
          });
          return;
        }
        const decoded = decodeMeshGeometryPayload(validated);
        if (controller.signal.aborted) return;
        setFetched({ key: requestKey, status: "ready", clip: decoded, errorMessage: null });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFetched({
          key: requestKey,
          status: "error",
          clip: null,
          errorMessage: "无法连接服务器获取 3D 网格几何数据。",
        });
      }
    })();

    return () => controller.abort();
  }, [jobId, startMs, endMs, requestKey, preloadedClip]);

  const clip = preloadedClip ?? (fetched?.key === requestKey ? fetched.clip : null);
  const status: "loading" | "ready" | "unavailable" | "error" = preloadedClip
    ? "ready"
    : fetched?.key === requestKey
      ? fetched.status
      : "loading";
  const errorMessage = fetched?.key === requestKey ? fetched.errorMessage : null;
  const clipKey = clip ? `${requestKey}:${clip.vertexCount}:${clip.frames.length}` : "";
  const webglUnavailable = !webglSupported || (clipKey !== "" && webglFailedKey === clipKey);

  const defaultFrameIndex = useMemo(() => {
    if (!clip) return 0;
    if (typeof initialTimeMs === "number" && Number.isFinite(initialTimeMs)) {
      return findNearestFrameIndex(clip.frames, initialTimeMs);
    }
    return 0;
  }, [clip, initialTimeMs]);

  const activeFrameIndex = clip
    ? Math.max(0, Math.min(clip.frames.length - 1, frameState.clipKey === clipKey ? frameState.index : defaultFrameIndex))
    : 0;

  useEffect(() => {
    wireframeRef.current = wireframe;
  }, [wireframe]);

  useEffect(() => {
    playingRef.current = playing;
  }, [playing]);

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);

  useEffect(() => {
    frameIndexRef.current = activeFrameIndex;
    if (clip && clip.frames[activeFrameIndex]) {
      clipClockMsRef.current = clip.frames[activeFrameIndex].timestampMs;
    }
  }, [activeFrameIndex, clip]);

  const renderFrame = useCallback((targetFrameIdx: number) => {
    const res = glRef.current;
    if (!res || !clip) return;
    const { gl } = res;
    if (gl.isContextLost()) return;

    const frame = clip.frames[targetFrameIdx];
    if (!frame) return;

    if (res.uploadedFrameIndex !== targetFrameIdx) {
      const normals = computeVertexNormals(frame.vertices, clip.indices, res.normalScratch);
      gl.bindBuffer(gl.ARRAY_BUFFER, res.posBuffer);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, frame.vertices);
      gl.bindBuffer(gl.ARRAY_BUFFER, res.normBuffer);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, normals);
      res.uploadedFrameIndex = targetFrameIdx;
    }

    const width = gl.drawingBufferWidth || 1;
    const height = gl.drawingBufferHeight || 1;
    gl.viewport(0, 0, width, height);
    gl.clearColor(0.055, 0.086, 0.11, 1.0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);

    const { viewProjection, eye } = orbitViewProjectionMatrix(cameraRef.current, width / height);

    // 1. Subtle dark night-court floor grid for spatial orientation
    gl.useProgram(res.lineProgram);
    if (res.meshLoc.aNormal >= 0) {
      gl.disableVertexAttribArray(res.meshLoc.aNormal);
    }
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.uniformMatrix4fv(res.lineLoc.uViewProjection, false, viewProjection);
    gl.uniform4f(res.lineLoc.uLineColor, 0.651, 0.741, 0.859, 0.26);
    gl.uniform1f(res.lineLoc.uFadeRadius, 1.65);
    gl.bindBuffer(gl.ARRAY_BUFFER, res.gridBuffer);
    gl.enableVertexAttribArray(res.lineLoc.aPosition);
    gl.vertexAttribPointer(res.lineLoc.aPosition, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.LINES, 0, res.gridVertexCount);

    // 2. Smooth dual-light solid human surface in official SAM 3D light-blue
    gl.disable(gl.BLEND);
    gl.useProgram(res.meshProgram);
    gl.uniformMatrix4fv(res.meshLoc.uViewProjection, false, viewProjection);
    gl.uniform3f(
      res.meshLoc.uBaseColor,
      clip.baseColorRgb[0] / 255,
      clip.baseColorRgb[1] / 255,
      clip.baseColorRgb[2] / 255,
    );
    gl.uniform3f(res.meshLoc.uCameraPos, eye[0], eye[1], eye[2]);
    gl.uniform1f(res.meshLoc.uWireframeMode, wireframeRef.current ? 1.0 : 0.0);

    gl.bindBuffer(gl.ARRAY_BUFFER, res.posBuffer);
    gl.enableVertexAttribArray(res.meshLoc.aPosition);
    gl.vertexAttribPointer(res.meshLoc.aPosition, 3, gl.FLOAT, false, 0, 0);

    if (res.meshLoc.aNormal >= 0) {
      gl.bindBuffer(gl.ARRAY_BUFFER, res.normBuffer);
      gl.enableVertexAttribArray(res.meshLoc.aNormal);
      gl.vertexAttribPointer(res.meshLoc.aNormal, 3, gl.FLOAT, false, 0, 0);
    }

    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, res.triIndexBuffer);
    if (wireframeRef.current) {
      gl.enable(gl.POLYGON_OFFSET_FILL);
      gl.polygonOffset(1.0, 1.0);
    } else {
      gl.disable(gl.POLYGON_OFFSET_FILL);
    }
    gl.drawElements(gl.TRIANGLES, res.triCount, res.glIndexType, 0);
    gl.disable(gl.POLYGON_OFFSET_FILL);
    if (res.meshLoc.aNormal >= 0) {
      gl.disableVertexAttribArray(res.meshLoc.aNormal);
    }

    // 3. Optional crisp wireframe topology overlay
    if (wireframeRef.current) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(res.lineProgram);
      gl.uniformMatrix4fv(res.lineLoc.uViewProjection, false, viewProjection);
      gl.uniform4f(res.lineLoc.uLineColor, 0.85, 0.94, 1.0, 0.48);
      gl.uniform1f(res.lineLoc.uFadeRadius, 0.0);
      gl.bindBuffer(gl.ARRAY_BUFFER, res.posBuffer);
      gl.enableVertexAttribArray(res.lineLoc.aPosition);
      gl.vertexAttribPointer(res.lineLoc.aPosition, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, res.wireIndexBuffer);
      gl.drawElements(gl.LINES, res.wireCount, res.glIndexType, 0);
      gl.disable(gl.BLEND);
    }
  }, [clip]);

  // Sync with report-wide seek events (`rallymate:seek-video`).
  useEffect(() => {
    if (!clip || typeof window === "undefined") return;
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string }>).detail;
      if ((detail?.jobId && detail.jobId !== jobId)
        || typeof detail?.timestampMs !== "number"
        || !Number.isFinite(detail.timestampMs)) {
        return;
      }
      const nextIdx = findNearestFrameIndex(clip.frames, detail.timestampMs);
      frameIndexRef.current = nextIdx;
      clipClockMsRef.current = clip.frames[nextIdx].timestampMs;
      setFrameState({ clipKey, index: nextIdx });
      renderFrame(nextIdx);
    };
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => window.removeEventListener("rallymate:seek-video", onSeek);
  }, [clip, clipKey, jobId, renderFrame]);

  // Initialize native WebGL context, buffers, resize observer, wheel zoom, and animation loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !clip || !webglSupported) return;

    const failWebgl = () => {
      queueMicrotask(() => setWebglFailedKey(clipKey));
    };

    const gl = acquireMeshWebGLContext(canvas, clip.indexType);
    if (!gl) {
      failWebgl();
      return;
    }

    const meshProgram = linkProgram(gl, MESH_VERTEX_SHADER, MESH_FRAGMENT_SHADER);
    const lineProgram = linkProgram(gl, LINE_VERTEX_SHADER, LINE_FRAGMENT_SHADER);
    if (!meshProgram || !lineProgram) {
      if (meshProgram) gl.deleteProgram(meshProgram);
      if (lineProgram) gl.deleteProgram(lineProgram);
      failWebgl();
      return;
    }

    const posBuffer = gl.createBuffer();
    const normBuffer = gl.createBuffer();
    const triIndexBuffer = gl.createBuffer();
    const wireIndexBuffer = gl.createBuffer();
    const gridBuffer = gl.createBuffer();
    if (!posBuffer || !normBuffer || !triIndexBuffer || !wireIndexBuffer || !gridBuffer) {
      if (posBuffer) gl.deleteBuffer(posBuffer);
      if (normBuffer) gl.deleteBuffer(normBuffer);
      if (triIndexBuffer) gl.deleteBuffer(triIndexBuffer);
      if (wireIndexBuffer) gl.deleteBuffer(wireIndexBuffer);
      if (gridBuffer) gl.deleteBuffer(gridBuffer);
      gl.deleteProgram(meshProgram);
      gl.deleteProgram(lineProgram);
      failWebgl();
      return;
    }

    const initialVertices = clip.frames[frameIndexRef.current]?.vertices ?? clip.frames[0].vertices;
    const normalScratch = computeVertexNormals(initialVertices, clip.indices);
    const wireIndices = buildWireframeEdgeIndices(clip.indices, clip.indexType);
    const floorGrid = buildCourtFloorGrid(clip.boundsMin[1] - 0.015, 1.6, 12);

    gl.bindBuffer(gl.ARRAY_BUFFER, posBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, initialVertices, gl.DYNAMIC_DRAW);

    gl.bindBuffer(gl.ARRAY_BUFFER, normBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, normalScratch, gl.DYNAMIC_DRAW);

    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, triIndexBuffer);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, clip.indices, gl.STATIC_DRAW);

    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, wireIndexBuffer);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, wireIndices, gl.STATIC_DRAW);

    gl.bindBuffer(gl.ARRAY_BUFFER, gridBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, floorGrid, gl.STATIC_DRAW);

    const resources: GlResources = {
      gl,
      meshProgram,
      lineProgram,
      meshLoc: {
        aPosition: gl.getAttribLocation(meshProgram, "aPosition"),
        aNormal: gl.getAttribLocation(meshProgram, "aNormal"),
        uViewProjection: gl.getUniformLocation(meshProgram, "uViewProjection"),
        uBaseColor: gl.getUniformLocation(meshProgram, "uBaseColor"),
        uCameraPos: gl.getUniformLocation(meshProgram, "uCameraPos"),
        uWireframeMode: gl.getUniformLocation(meshProgram, "uWireframeMode"),
      },
      lineLoc: {
        aPosition: gl.getAttribLocation(lineProgram, "aPosition"),
        uViewProjection: gl.getUniformLocation(lineProgram, "uViewProjection"),
        uLineColor: gl.getUniformLocation(lineProgram, "uLineColor"),
        uFadeRadius: gl.getUniformLocation(lineProgram, "uFadeRadius"),
      },
      posBuffer,
      normBuffer,
      triIndexBuffer,
      wireIndexBuffer,
      gridBuffer,
      triCount: clip.indices.length,
      wireCount: wireIndices.length,
      gridVertexCount: floorGrid.length / 3,
      glIndexType: clip.indexType === "uint16" ? gl.UNSIGNED_SHORT : gl.UNSIGNED_INT,
      normalScratch,
      uploadedFrameIndex: frameIndexRef.current,
    };
    glRef.current = resources;

    const resizeCanvas = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const targetWidth = Math.max(1, Math.round(rect.width * dpr));
      const targetHeight = Math.max(1, Math.round(rect.height * dpr));
      if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
        canvas.width = targetWidth;
        canvas.height = targetHeight;
      }
      renderFrame(frameIndexRef.current);
    };
    resizeCanvas();

    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resizeCanvas) : null;
    observer?.observe(canvas);

    const onContextLost = (event: Event) => {
      event.preventDefault();
      setWebglFailedKey(clipKey);
    };
    canvas.addEventListener("webglcontextlost", onContextLost);

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = Math.exp(event.deltaY * 0.0014);
      cameraRef.current = {
        ...cameraRef.current,
        distance: clampOrbitDistance(cameraRef.current.distance * factor),
      };
      setActivePreset(null);
      renderFrame(frameIndexRef.current);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });

    const firstTs = clip.frames[0].timestampMs;
    const lastTs = clip.frames[clip.frames.length - 1].timestampMs;
    const clipDurationMs = Math.max(0, lastTs - firstTs);
    let lastRafTime: number | null = null;
    let rafId = 0;

    const tick = (now: number) => {
      if (lastRafTime !== null && playingRef.current && clip.frames.length > 1) {
        const dtMs = Math.min(now - lastRafTime, 120);
        if (clipDurationMs > 1) {
          let nextMs = clipClockMsRef.current + dtMs * speedRef.current;
          if (nextMs > lastTs) {
            nextMs = firstTs + ((nextMs - firstTs) % clipDurationMs);
          }
          clipClockMsRef.current = nextMs;
          const nextIdx = findNearestFrameIndex(clip.frames, nextMs);
          if (nextIdx !== frameIndexRef.current) {
            frameIndexRef.current = nextIdx;
            setFrameState({ clipKey, index: nextIdx });
          }
        } else {
          // Fallback fixed ~15fps stepping if timestamps are identical.
          const stepMs = 66 / Math.max(0.1, speedRef.current);
          clipClockMsRef.current += dtMs;
          if (clipClockMsRef.current >= stepMs) {
            clipClockMsRef.current = 0;
            const nextIdx = (frameIndexRef.current + 1) % clip.frames.length;
            frameIndexRef.current = nextIdx;
            setFrameState({ clipKey, index: nextIdx });
          }
        }
      }
      lastRafTime = now;
      renderFrame(frameIndexRef.current);
      rafId = window.requestAnimationFrame(tick);
    };
    rafId = window.requestAnimationFrame(tick);

    return () => {
      window.cancelAnimationFrame(rafId);
      observer?.disconnect();
      canvas.removeEventListener("webglcontextlost", onContextLost);
      canvas.removeEventListener("wheel", onWheel);
      gl.deleteBuffer(posBuffer);
      gl.deleteBuffer(normBuffer);
      gl.deleteBuffer(triIndexBuffer);
      gl.deleteBuffer(wireIndexBuffer);
      gl.deleteBuffer(gridBuffer);
      gl.deleteProgram(meshProgram);
      gl.deleteProgram(lineProgram);
      if (glRef.current === resources) {
        glRef.current = null;
      }
    };
  }, [clip, clipKey, renderFrame, webglSupported]);

  const applyPreset = (preset: OrbitViewPreset) => {
    cameraRef.current = orbitPresetState(preset);
    setActivePreset(preset === "reset" ? "front" : preset);
    renderFrame(frameIndexRef.current);
  };

  const adjustZoom = (factor: number) => {
    cameraRef.current = {
      ...cameraRef.current,
      distance: clampOrbitDistance(cameraRef.current.distance * factor),
    };
    setActivePreset(null);
    renderFrame(frameIndexRef.current);
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointersRef.current.size === 1) {
      setDragging(true);
    } else if (pointersRef.current.size === 2) {
      const pts = Array.from(pointersRef.current.values());
      pinchDistanceRef.current = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
    }
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const prev = pointersRef.current.get(event.pointerId);
    if (!prev) return;
    const dx = event.clientX - prev.x;
    const dy = event.clientY - prev.y;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (pointersRef.current.size === 1) {
      cameraRef.current = {
        ...cameraRef.current,
        yaw: cameraRef.current.yaw - dx * 0.0085,
        pitch: clampOrbitPitch(cameraRef.current.pitch + dy * 0.0075),
      };
      setActivePreset(null);
      renderFrame(frameIndexRef.current);
    } else if (pointersRef.current.size === 2) {
      const pts = Array.from(pointersRef.current.values());
      const dist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
      if (pinchDistanceRef.current && dist > 1) {
        const scale = pinchDistanceRef.current / dist;
        cameraRef.current = {
          ...cameraRef.current,
          distance: clampOrbitDistance(cameraRef.current.distance * scale),
        };
        setActivePreset(null);
        renderFrame(frameIndexRef.current);
      }
      pinchDistanceRef.current = dist;
    }
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size < 2) {
      pinchDistanceRef.current = null;
    }
    if (pointersRef.current.size === 0) {
      setDragging(false);
    }
  };

  const handleCanvasKeyDown = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      cameraRef.current = { ...cameraRef.current, yaw: cameraRef.current.yaw + 0.14 };
      setActivePreset(null);
      renderFrame(frameIndexRef.current);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      cameraRef.current = { ...cameraRef.current, yaw: cameraRef.current.yaw - 0.14 };
      setActivePreset(null);
      renderFrame(frameIndexRef.current);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      cameraRef.current = { ...cameraRef.current, pitch: clampOrbitPitch(cameraRef.current.pitch + 0.1) };
      setActivePreset(null);
      renderFrame(frameIndexRef.current);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      cameraRef.current = { ...cameraRef.current, pitch: clampOrbitPitch(cameraRef.current.pitch - 0.1) };
      setActivePreset(null);
      renderFrame(frameIndexRef.current);
    } else if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      adjustZoom(0.88);
    } else if (event.key === "-") {
      event.preventDefault();
      adjustZoom(1.14);
    } else if (event.key === " ") {
      event.preventDefault();
      setPlaying(prev => !prev);
    } else if (event.key === "r" || event.key === "R") {
      event.preventDefault();
      applyPreset("reset");
    }
  };

  const currentFrame = clip?.frames[activeFrameIndex] ?? null;

  if (webglUnavailable) {
    return (
      <div
        className="interactive-mesh-viewer"
        data-mesh-viewer-state="webgl-unsupported"
        role="status"
        style={{
          padding: "28px 22px",
          borderRadius: 12,
          background: "#0f171e",
          color: "#dbe6f2",
          border: "1px solid rgba(166, 189, 219, 0.22)",
        }}
      >
        <p style={{ margin: "0 0 12px", fontSize: 13, lineHeight: 1.7 }}>
          当前浏览器或图形环境不支持 WebGL 3D 自由视角，官方人体网格视频与评分报告仍可正常使用。
        </p>
        {onFallbackToVideo && (
          <button type="button" className="product-secondary" onClick={onFallbackToVideo}>
            返回原片叠加视频
          </button>
        )}
      </div>
    );
  }

  if (status === "loading") {
    return (
      <div
        className="interactive-mesh-viewer"
        data-mesh-viewer-state="loading"
        role="status"
        style={{
          minHeight: 320,
          display: "grid",
          placeItems: "center",
          padding: 24,
          borderRadius: 12,
          background: "radial-gradient(circle at 50% 35%, #16242f 0%, #0d151b 100%)",
          color: "#c7d9ec",
          border: "1px solid rgba(166, 189, 219, 0.18)",
        }}
      >
        <div style={{ textAlign: "center" }}>
          <p style={{ margin: 0, fontSize: 13 }}>正在读取 3D 人体网格几何数据…</p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "rgba(199, 217, 236, 0.65)" }}>
            加载完成后可 360° 自由旋转与逐帧审阅动作姿态
          </p>
        </div>
      </div>
    );
  }

  if (status === "unavailable" || status === "error" || !clip) {
    return (
      <div
        className="interactive-mesh-viewer"
        data-mesh-viewer-state={status}
        role="status"
        style={{
          padding: "28px 22px",
          borderRadius: 12,
          background: "#0f171e",
          color: "#dbe6f2",
          border: "1px solid rgba(166, 189, 219, 0.22)",
        }}
      >
        <p style={{ margin: "0 0 14px", fontSize: 13, lineHeight: 1.7 }}>
          {errorMessage ?? "3D 自由视角几何数据暂不可用。"}原片叠加、侧面 90° 视频回放与评分报告仍可正常查看。
        </p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button
            type="button"
            className="product-secondary"
            onClick={() => setAttempt(value => value + 1)}
          >
            重新加载 3D 网格
          </button>
          {onFallbackToVideo && (
            <button type="button" className="product-secondary" onClick={onFallbackToVideo}>
              切换回原片叠加
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className="interactive-mesh-viewer"
      data-mesh-viewer-state="ready"
      data-mesh-job-id={clip.jobId}
      style={{
        borderRadius: 12,
        overflow: "hidden",
        background: "#0d151b",
        border: "1px solid rgba(166, 189, 219, 0.22)",
        color: "#e6eef7",
      }}
    >
      {/* Top 3D camera presets & display controls */}
      <div
        role="toolbar"
        aria-label="3D 视角预设与显示控制"
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          padding: "10px 14px",
          background: "rgba(16, 25, 32, 0.94)",
          borderBottom: "1px solid rgba(166, 189, 219, 0.14)",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }} role="group" aria-label="相机角度预设">
          {VIEW_PRESETS.map(preset => {
            const isActive = preset.id !== "reset" && activePreset === preset.id;
            return (
              <button
                key={preset.id}
                type="button"
                className="product-secondary"
                aria-pressed={preset.id === "reset" ? undefined : isActive}
                onClick={() => applyPreset(preset.id)}
                style={{
                  fontSize: 12,
                  padding: "4px 10px",
                  borderRadius: 6,
                  border: isActive ? "1px solid #c9ff43" : "1px solid rgba(166, 189, 219, 0.28)",
                  background: isActive ? "rgba(201, 255, 67, 0.14)" : "rgba(255, 255, 255, 0.04)",
                  color: isActive ? "#c9ff43" : "#dce7f3",
                  cursor: "pointer",
                }}
              >
                {preset.label}
              </button>
            );
          })}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <button
            type="button"
            aria-label="放大 3D 视角"
            onClick={() => adjustZoom(0.86)}
            style={{
              fontSize: 12,
              padding: "4px 9px",
              borderRadius: 6,
              border: "1px solid rgba(166, 189, 219, 0.28)",
              background: "rgba(255, 255, 255, 0.04)",
              color: "#dce7f3",
              cursor: "pointer",
            }}
          >
            ＋
          </button>
          <button
            type="button"
            aria-label="缩小 3D 视角"
            onClick={() => adjustZoom(1.16)}
            style={{
              fontSize: 12,
              padding: "4px 9px",
              borderRadius: 6,
              border: "1px solid rgba(166, 189, 219, 0.28)",
              background: "rgba(255, 255, 255, 0.04)",
              color: "#dce7f3",
              cursor: "pointer",
            }}
          >
            －
          </button>
          <button
            type="button"
            aria-pressed={wireframe}
            onClick={() => setWireframe(value => !value)}
            style={{
              fontSize: 12,
              padding: "4px 10px",
              borderRadius: 6,
              border: wireframe ? "1px solid #a6bddb" : "1px solid rgba(166, 189, 219, 0.28)",
              background: wireframe ? "rgba(166, 189, 219, 0.22)" : "rgba(255, 255, 255, 0.04)",
              color: wireframe ? "#ffffff" : "#dce7f3",
              cursor: "pointer",
            }}
          >
            线框
          </button>
        </div>
      </div>

      {/* Interactive WebGL viewport */}
      <div
        style={{
          position: "relative",
          width: "100%",
          height: "clamp(320px, 56vh, 560px)",
          background: "radial-gradient(circle at 50% 42%, #15232e 0%, #0d151b 78%)",
        }}
      >
        <canvas
          ref={canvasRef}
          tabIndex={0}
          aria-label="SAM 3D Body 360 度交互式人体网格视图，支持拖拽旋转、滚轮或双指缩放以及方向键调整视角"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onKeyDown={handleCanvasKeyDown}
          style={{
            width: "100%",
            height: "100%",
            display: "block",
            touchAction: "none",
            cursor: dragging ? "grabbing" : "grab",
          }}
        />
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 10,
            left: 12,
            pointerEvents: "none",
            fontSize: 11,
            fontFamily: "var(--mono, monospace)",
            color: "rgba(166, 189, 219, 0.85)",
            background: "rgba(11, 18, 24, 0.65)",
            padding: "4px 8px",
            borderRadius: 6,
          }}
        >
          SAM 3D · {clip.vertexCount.toLocaleString()}V / {clip.faceCount.toLocaleString()}F
        </div>
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            bottom: 10,
            left: 12,
            pointerEvents: "none",
            fontSize: 11,
            color: "rgba(210, 224, 238, 0.66)",
            background: "rgba(11, 18, 24, 0.58)",
            padding: "3px 8px",
            borderRadius: 6,
          }}
        >
          拖拽 360° 旋转 · 滚轮/双指缩放
        </div>
      </div>

      {/* Bottom playback & timeline scrubber bar */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 10,
          padding: "10px 14px",
          background: "rgba(14, 22, 29, 0.96)",
          borderTop: "1px solid rgba(166, 189, 219, 0.14)",
        }}
      >
        <button
          type="button"
          aria-label={playing ? "暂停 3D 回放" : "播放 3D 回放"}
          onClick={() => setPlaying(value => !value)}
          disabled={clip.frames.length <= 1}
          style={{
            fontSize: 12,
            fontWeight: 600,
            padding: "5px 12px",
            borderRadius: 6,
            border: "1px solid rgba(201, 255, 67, 0.55)",
            background: "rgba(201, 255, 67, 0.14)",
            color: "#c9ff43",
            cursor: clip.frames.length <= 1 ? "not-allowed" : "pointer",
            opacity: clip.frames.length <= 1 ? 0.5 : 1,
          }}
        >
          {playing ? "暂停" : "播放"}
        </button>

        <div style={{ display: "flex", gap: 4 }} role="group" aria-label="3D 播放倍速">
          {PLAYBACK_SPEEDS.map(rate => {
            const selected = speed === rate;
            return (
              <button
                key={rate}
                type="button"
                aria-pressed={selected}
                onClick={() => setSpeed(rate)}
                style={{
                  fontSize: 11,
                  padding: "4px 8px",
                  borderRadius: 5,
                  border: selected ? "1px solid #c9ff43" : "1px solid rgba(166, 189, 219, 0.22)",
                  background: selected ? "rgba(201, 255, 67, 0.16)" : "transparent",
                  color: selected ? "#c9ff43" : "#b9cadc",
                  cursor: "pointer",
                }}
              >
                {rate}×
              </button>
            );
          })}
        </div>

        <input
          type="range"
          aria-label="3D 网格帧时间轴"
          min={0}
          max={Math.max(0, clip.frames.length - 1)}
          value={activeFrameIndex}
          disabled={clip.frames.length <= 1}
          onChange={event => {
            const nextIdx = Number(event.target.value);
            if (!Number.isSafeInteger(nextIdx)) return;
            frameIndexRef.current = nextIdx;
            if (clip.frames[nextIdx]) {
              clipClockMsRef.current = clip.frames[nextIdx].timestampMs;
            }
            setFrameState({ clipKey, index: nextIdx });
            renderFrame(nextIdx);
          }}
          style={{
            flex: "1 1 160px",
            accentColor: "#c9ff43",
            cursor: clip.frames.length <= 1 ? "default" : "pointer",
          }}
        />

        <span
          style={{
            fontSize: 11,
            fontFamily: "var(--mono, monospace)",
            color: "#b9cadc",
            whiteSpace: "nowrap",
          }}
        >
          {formatMeshTimeMs(currentFrame?.timestampMs ?? 0)} · {activeFrameIndex + 1}/{clip.frames.length}
        </span>
      </div>
    </div>
  );
}
