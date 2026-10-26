export type OfficialMeshMedia = {
  status: "ready";
  video_url: string;
  side_video_url: string;
  preview_image_url: string;
  manifest_url: string;
  timing_preserved: true;
  source_timestamp_origin_ms: number;
  source_relative_origin_ms: number;
  source_video_sha256: string;
  frame_count: number;
  renderer: string;
};

/** Rebuild authenticated routes; imported JSON cannot certify its own media. */
export function verifiedMeshMedia(value: unknown, jobId: string | undefined, online: boolean): OfficialMeshMedia | null {
  if (!online || !jobId || !value || typeof value !== "object" || Array.isArray(value)) return null;
  const media = value as Record<string, unknown>;
  if (media.status !== "ready" || media.timing_preserved !== true
    || typeof media.source_video_sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(media.source_video_sha256)
    || !Number.isSafeInteger(media.frame_count) || (media.frame_count as number) < 1
    || typeof media.source_timestamp_origin_ms !== "number" || !Number.isFinite(media.source_timestamp_origin_ms)
    || typeof media.source_relative_origin_ms !== "number" || !Number.isFinite(media.source_relative_origin_ms) || media.source_relative_origin_ms < 0
    || media.renderer !== "official-sam3d-pyrender-persistent-context") return null;
  const base = `/v1/jobs/${encodeURIComponent(jobId)}/artifacts`;
  for (const [key, name] of [["video_url", "sam3d-mesh.mp4"], ["side_video_url", "sam3d-mesh-side.mp4"],
    ["preview_image_url", "sam3d-mesh-preview.jpg"], ["manifest_url", "sam3d-mesh-manifest.json"]]) {
    if (typeof media[key] !== "string") return null;
    try {
      const url = new URL(media[key] as string, "https://rallymate.invalid");
      if (url.pathname !== `${base}/${name}` || !["http:", "https:"].includes(url.protocol)
        || url.search || url.hash || url.username || url.password) return null;
    } catch { return null; }
  }
  return { ...media, video_url: `${base}/sam3d-mesh.mp4`, side_video_url: `${base}/sam3d-mesh-side.mp4`,
    preview_image_url: `${base}/sam3d-mesh-preview.jpg`, manifest_url: `${base}/sam3d-mesh-manifest.json` } as OfficialMeshMedia;
}

function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Poll only the small mesh status resource, never the expanded numerical report. */
export async function watchMeshPreview(
  getMeshPreview: (jobId: string, signal?: AbortSignal) => Promise<Record<string, unknown>>,
  jobId: string, signal: AbortSignal,
  update: (state: { status: "pending" | "ready" | "failed" | "unavailable"; media: OfficialMeshMedia | null }) => void,
  intervalMs = 10000,
): Promise<void> {
  const readSignal = AbortSignal.any([signal, AbortSignal.timeout(120000)]);
  let failures = 0;
  for (;;) {
    readSignal.throwIfAborted();
    let job: Record<string, unknown>;
    try { job = await getMeshPreview(jobId, readSignal); failures = 0; }
    catch (error) {
      readSignal.throwIfAborted();
      const status = error && typeof error === "object" && "status" in error ? Number(error.status) : 0;
      if (++failures >= 3 || status && status !== 408 && status !== 429 && status < 500) throw error;
      await pause(intervalMs, readSignal);
      continue;
    }
    if (signal.aborted) return;
    readSignal.throwIfAborted();
    if (job.job_id !== jobId) throw new Error("Mesh preview belongs to another training session");
    const media = verifiedMeshMedia(job.sam3d_mesh, jobId, true);
    const status = media ? "ready" : job.status === "pending" ? "pending"
      : job.status === "failed" || job.status === "ready" ? "failed" : "unavailable";
    update({ status, media });
    if (status !== "pending") return;
    await pause(intervalMs, readSignal);
  }
}
