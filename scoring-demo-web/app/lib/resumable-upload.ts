import { RallyMateApiError, type JobSubmission, type SubmitVideoOptions } from "./api-types";

export type UploadProgress = {
  uploadedBytes: number; totalBytes: number; percent: number; bytesPerSecond: number;
  phase: "preparing" | "uploading" | "waiting" | "retrying" | "merging";
  message: string; resumed: boolean; retryAttempt: number;
};
type UploadSession = { upload_id: string; size: number; chunk_size: number; chunk_count: number; received_chunks: number[]; chunk_sha256: Record<string, string>; received_bytes: number; job_id: string | null };

async function digest(blob: Blob) {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())), b => b.toString(16).padStart(2, "0")).join("");
}

function pause(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal?.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal?.reason); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/** Persisted server-sized byte chunks, two in flight. Progress counts confirmed bytes. */
export async function uploadResumable(file: File | Blob, options: SubmitVideoOptions, urlFor: (path: string) => string, fetchImpl: typeof fetch, parse: <T>(r: Response) => Promise<T>): Promise<JobSubmission> {
  const received = new Set<number>();
  const knownDigests = new Map<number, string>();
  const waiting = new Set<symbol>();
  const retrying = new Map<symbol, number>();
  const started = Date.now();
  let session: UploadSession | undefined;
  let stage: "preparing" | "uploading" | "merging" = "preparing";
  let preparationMessage = "正在准备视频";
  let initialBytes = 0;
  let resumed = false;
  const progress = () => {
    const uploadedBytes = session ? [...received].reduce((sum, index) => sum + Math.min(session!.chunk_size, file.size - index * session!.chunk_size), 0) : 0;
    const retryAttempt = Math.max(0, ...retrying.values());
    const phase = retryAttempt ? "retrying" : waiting.size ? "waiting" : stage;
    const message = phase === "retrying" ? `连接中断，正在自动重试（第 ${retryAttempt} 次）`
      : phase === "waiting" ? stage === "merging" ? "视频已上传，正在等待校验完成" : "正在等待服务器确认"
      : stage === "preparing" ? preparationMessage
      : stage === "merging" ? "正在校验视频"
      : resumed ? "正在继续上传" : "正在上传视频";
    options.onUploadProgress?.({ uploadedBytes, totalBytes: file.size, percent: file.size ? Math.min(100, 100 * uploadedBytes / file.size) : 0, bytesPerSecond: Math.max(0, uploadedBytes - initialBytes) / Math.max(0.1, (Date.now() - started) / 1000), phase, message, resumed, retryAttempt });
  };
  progress();
  const filename = typeof File !== "undefined" && file instanceof File ? file.name : "rallymate-video.mp4";
  const analysisOptions = { court_mode: options.courtMode ?? "disabled", write_annotated_video: options.writeAnnotatedVideo ?? true, ...options.metadata };
  const signature = await digest(new Blob([JSON.stringify({ name: filename, size: file.size, options: analysisOptions }), file.slice(0, 65536), file.slice(Math.max(0, file.size - 65536))]));
  const storageKey = `rallymate.upload.v1.${signature}`;
  let uploadId = crypto.randomUUID();
  try { const saved = localStorage.getItem(storageKey); if (saved && /^[0-9a-f-]{36}$/.test(saved)) uploadId = saved as `${string}-${string}-${string}-${string}-${string}`; } catch { /* Private mode still supports in-tab retries. */ }
  const remember = () => { try { localStorage.setItem(storageKey, uploadId); } catch { /* Optional storage. */ } };
  remember();
  const perform = async <T>(path: string, init: RequestInit, timeout: number): Promise<T> => parse<T>(await fetchImpl(urlFor(path), {
    ...init, cache: "no-store", headers: { "X-Request-ID": crypto.randomUUID(), ...init.headers },
    signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
  }));
  const request = async <T>(path: string, init: RequestInit = {}, timeout = 90000, recover?: () => Promise<T | undefined>): Promise<T> => {
    const token = Symbol();
    try {
      for (let attempt = 0; ; attempt++) {
        options.signal?.throwIfAborted();
        retrying.delete(token);
        progress();
        try {
          const timer = setTimeout(() => { waiting.add(token); progress(); }, 10000);
          try { return await perform<T>(path, init, timeout); }
          finally { clearTimeout(timer); waiting.delete(token); }
        } catch (error) {
          options.signal?.throwIfAborted();
          const retryable = !(error instanceof RallyMateApiError) || [408, 425, 429, 500, 502, 503, 504].includes(error.status ?? 0);
          if (!retryable) throw error;
          retrying.set(token, attempt + 1);
          progress();
          if (recover) {
            try { const recovered = await recover(); if (recovered !== undefined) return recovered; }
            catch { options.signal?.throwIfAborted(); }
          }
          if (attempt >= 4) throw new Error("网络连接中断。已上传的分块会保留 24 小时；点击继续上传即可续传，无需从头开始。");
          await pause(Math.min(8000, 700 * 2 ** attempt), options.signal);
        }
      }
    } finally { waiting.delete(token); retrying.delete(token); }
  };
  const create = () => request<UploadSession>("/v1/uploads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ upload_id: uploadId, filename, size: file.size, options: analysisOptions }) });
  preparationMessage = "正在连接上传服务";
  try { session = await create(); } catch (error) {
    if (!(error instanceof RallyMateApiError) || ![404, 410].includes(error.status ?? 0)) throw error;
    uploadId = crypto.randomUUID(); remember(); session = await create();
  }
  // The small fingerprint finds a possible session; only matching every saved
  // chunk proves identity. Equal names/sizes/endpoints can hide changed middle bytes.
  preparationMessage = "正在核验已上传片段";
  progress();
  for (const index of session.received_chunks) {
    options.signal?.throwIfAborted();
    const checksum = await digest(file.slice(index * session.chunk_size, Math.min(file.size, (index + 1) * session.chunk_size)));
    if (session.chunk_sha256?.[String(index)] !== checksum) {
      uploadId = crypto.randomUUID(); remember(); session = await create(); break;
    }
  }
  session.received_chunks.forEach(index => received.add(index));
  resumed = received.size > 0;
  initialBytes = session.received_bytes;
  stage = "uploading";
  progress();
  const activeSession = session;
  const acknowledge = (snapshot: UploadSession) => {
    if (snapshot.upload_id !== uploadId || snapshot.size !== file.size || snapshot.chunk_size !== activeSession.chunk_size || snapshot.chunk_count !== activeSession.chunk_count || !Array.isArray(snapshot.received_chunks)) return;
    for (const index of snapshot.received_chunks) {
      const expected = knownDigests.get(index);
      if (expected && snapshot.chunk_sha256?.[String(index)] === expected) received.add(index);
    }
  };
  const pending = Array.from({ length: session.chunk_count }, (_, i) => i).filter(i => !received.has(i));
  let cursor = 0;
  let stopped = false;
  const send = async () => {
    while (!stopped && cursor < pending.length) {
      const index = pending[cursor++];
      if (received.has(index)) continue;
      const chunk = file.slice(index * activeSession.chunk_size, Math.min(file.size, (index + 1) * activeSession.chunk_size));
      try {
        const checksum = await digest(chunk);
        knownDigests.set(index, checksum);
        const confirmation = await request<UploadSession>(`/v1/uploads/${uploadId}/chunks/${index}`, { method: "PUT", headers: { "Content-Type": "application/octet-stream", "X-Chunk-SHA256": checksum }, body: chunk }, 90000, async () => {
          // A lost PUT response does not imply lost bytes. Read durable status
          // before resending, and only merge matching checksums monotonically.
          const snapshot = await perform<UploadSession>(`/v1/uploads/${uploadId}`, { method: "GET" }, 10000);
          acknowledge(snapshot);
          progress();
          return received.has(index) ? snapshot : undefined;
        });
        acknowledge(confirmation);
        if (!received.has(index)) throw new RallyMateApiError("服务器尚未确认这个视频分块，请继续上传重试。", 502);
        progress();
      } catch (error) { stopped = true; throw error; }
    }
  };
  // Await both workers on errors as well; a retry cannot overlap a surviving worker.
  const workers = await Promise.allSettled([send(), send()]);
  const failed = workers.find(r => r.status === "rejected");
  if (failed?.status === "rejected") throw failed.reason;
  stage = "merging";
  progress();
  const job = await request<JobSubmission>(`/v1/uploads/${uploadId}/complete`, { method: "POST" }, 90000);
  try { localStorage.removeItem(storageKey); } catch { /* Optional storage. */ }
  return job;
}
