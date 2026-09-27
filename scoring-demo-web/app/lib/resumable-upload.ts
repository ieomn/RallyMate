import { RallyMateApiError, type JobSubmission, type SubmitVideoOptions } from "./api-types";

export type UploadProgress = { uploadedBytes: number; totalBytes: number; percent: number; bytesPerSecond: number; phase: "uploading" | "merging"; resumed: boolean };
type UploadSession = { upload_id: string; chunk_size: number; chunk_count: number; received_chunks: number[]; chunk_sha256: Record<string, string>; received_bytes: number; job_id: string | null };

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

/** Persisted 4 MiB byte chunks, two in flight. Retrying never resends completed chunks. */
export async function uploadResumable(file: File | Blob, options: SubmitVideoOptions, urlFor: (path: string) => string, fetchImpl: typeof fetch, parse: <T>(r: Response) => Promise<T>): Promise<JobSubmission> {
  const filename = typeof File !== "undefined" && file instanceof File ? file.name : "rallymate-video.mp4";
  const analysisOptions = { court_mode: options.courtMode ?? "disabled", write_annotated_video: options.writeAnnotatedVideo ?? true, ...options.metadata };
  const signature = await digest(new Blob([JSON.stringify({ name: filename, size: file.size, options: analysisOptions }), file.slice(0, 65536), file.slice(Math.max(0, file.size - 65536))]));
  const storageKey = `rallymate.upload.v1.${signature}`;
  let uploadId = crypto.randomUUID();
  try { const saved = localStorage.getItem(storageKey); if (saved && /^[0-9a-f-]{36}$/.test(saved)) uploadId = saved as `${string}-${string}-${string}-${string}-${string}`; } catch { /* Private mode still supports in-tab retries. */ }
  const remember = () => { try { localStorage.setItem(storageKey, uploadId); } catch { /* Optional storage. */ } };
  remember();
  const request = async <T>(path: string, init: RequestInit = {}, timeout = 90000): Promise<T> => {
    for (let attempt = 0; ; attempt++) {
      options.signal?.throwIfAborted();
      try {
        return await parse<T>(await fetchImpl(urlFor(path), {
          ...init, cache: "no-store", headers: { "X-Request-ID": crypto.randomUUID(), ...init.headers },
          signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
        }));
      } catch (error) {
        options.signal?.throwIfAborted();
        const retryable = !(error instanceof RallyMateApiError) || [408, 425, 429, 500, 502, 503, 504].includes(error.status ?? 0);
        if (!retryable) throw error;
        if (attempt >= 4) throw new Error("网络连接中断。已上传的分块会保留 24 小时；点击继续上传即可续传，无需从头开始。");
        await pause(Math.min(8000, 700 * 2 ** attempt), options.signal);
      }
    }
  };
  const create = () => request<UploadSession>("/v1/uploads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ upload_id: uploadId, filename, size: file.size, options: analysisOptions }) });
  let session: UploadSession;
  try { session = await create(); } catch (error) {
    if (!(error instanceof RallyMateApiError) || ![404, 410].includes(error.status ?? 0)) throw error;
    uploadId = crypto.randomUUID(); remember(); session = await create();
  }
  // The small fingerprint finds a possible session; only matching every saved
  // chunk proves identity. Equal names/sizes/endpoints can hide changed middle bytes.
  for (const index of session.received_chunks) {
    options.signal?.throwIfAborted();
    const checksum = await digest(file.slice(index * session.chunk_size, Math.min(file.size, (index + 1) * session.chunk_size)));
    if (session.chunk_sha256?.[String(index)] !== checksum) {
      uploadId = crypto.randomUUID(); remember(); session = await create(); break;
    }
  }
  const received = new Set(session.received_chunks);
  const resumed = received.size > 0;
  const started = Date.now();
  const initialBytes = session.received_bytes;
  const progress = (phase: UploadProgress["phase"]) => {
    const uploadedBytes = [...received].reduce((sum, index) => sum + Math.min(session.chunk_size, file.size - index * session.chunk_size), 0);
    options.onUploadProgress?.({ uploadedBytes, totalBytes: file.size, percent: Math.min(100, 100 * uploadedBytes / file.size), bytesPerSecond: (uploadedBytes - initialBytes) / Math.max(0.1, (Date.now() - started) / 1000), phase, resumed });
  };
  progress("uploading");
  const pending = Array.from({ length: session.chunk_count }, (_, i) => i).filter(i => !received.has(i));
  let cursor = 0;
  let stopped = false;
  const send = async () => {
    while (!stopped && cursor < pending.length) {
      const index = pending[cursor++];
      const chunk = file.slice(index * session.chunk_size, Math.min(file.size, (index + 1) * session.chunk_size));
      try {
        const checksum = await digest(chunk);
        await request(`/v1/uploads/${uploadId}/chunks/${index}`, { method: "PUT", headers: { "Content-Type": "application/octet-stream", "X-Chunk-SHA256": checksum }, body: chunk });
        received.add(index);
        progress("uploading");
      } catch (error) { stopped = true; throw error; }
    }
  };
  // Await both workers on errors as well; a retry cannot overlap a surviving worker.
  const workers = await Promise.allSettled([send(), send()]);
  const failed = workers.find(r => r.status === "rejected");
  if (failed?.status === "rejected") throw failed.reason;
  progress("merging");
  const job = await request<JobSubmission>(`/v1/uploads/${uploadId}/complete`, { method: "POST" }, 90000);
  try { localStorage.removeItem(storageKey); } catch { /* Optional storage. */ }
  return job;
}
