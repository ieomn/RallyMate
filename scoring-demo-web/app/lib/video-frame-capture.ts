type CaptureVideo = Pick<HTMLVideoElement, "currentTime" | "readyState" | "videoWidth" | "videoHeight" | "seeking" | "pause" | "addEventListener" | "removeEventListener"> & Partial<Pick<HTMLVideoElement, "preload" | "src" | "currentSrc">>;
type CaptureOptions = { signal?: AbortSignal; timeoutMs?: number };

/** Wait for the selected decoded frame without playing or moving time.
 * Canvas uses the video frame at its current playback position, not the last
 * compositor paint. HAVE_CURRENT_DATA and a completed seek permit a canvas
 * attempt even when animation/video-frame callbacks are not delivered.
 * https://html.spec.whatwg.org/multipage/canvas.html#image-sources-for-2d-rendering-contexts
 * The caller must still handle draw/serialization errors and task changes. */
export function waitForPresentedVideoFrame(video: CaptureVideo, options: CaptureOptions = {}): Promise<boolean> {
  const targetTime = video.currentTime;
  if (options.signal?.aborted || !Number.isFinite(targetTime) || targetTime < 0) return Promise.resolve(false);
  // A replacement src can coexist with the previous decoded resource until it loads.
  const sourcesAgree = () => !video.src || !video.currentSrc || video.src === video.currentSrc;
  if (!sourcesAgree()) return Promise.resolve(false);
  video.pause();
  const source = video.src, currentSource = video.currentSrc;
  const originalPreload = video.preload;
  const sameSource = () => sourcesAgree() && video.src === source && (!currentSource || video.currentSrc === currentSource);
  return new Promise(resolve => {
    let settled = false, seekCompleted = !video.seeking, preloadChanged = false;
    const readinessEvents = ["loadedmetadata", "loadeddata", "canplay", "timeupdate"];
    const timer = setTimeout(() => finish(false), options.timeoutMs ?? 5000);
    const finish = (success: boolean) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      for (const name of readinessEvents) video.removeEventListener(name, verify);
      video.removeEventListener("seeking", onSeeking);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
      video.removeEventListener("emptied", onError);
      options.signal?.removeEventListener("abort", onAbort);
      if (preloadChanged && sameSource() && video.preload === "auto") video.preload = originalPreload!;
      resolve(success);
    };
    const sameTime = () => Math.abs(video.currentTime - targetTime) < .001;
    const verify = () => {
      if (settled) return;
      if (options.signal?.aborted || !sameSource() || !sameTime()) { finish(false); return; }
      if (seekCompleted && !video.seeking && video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) finish(true);
    };
    const onError = () => finish(false);
    const onAbort = () => finish(false);
    const onSeeking = () => { seekCompleted = false; verify(); };
    const onSeeked = () => { seekCompleted = true; verify(); };
    for (const name of readinessEvents) video.addEventListener(name, verify);
    video.addEventListener("seeking", onSeeking);
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", onError);
    video.addEventListener("emptied", onError);
    options.signal?.addEventListener("abort", onAbort, { once: true });
    // Ask metadata-only media to decode without load(), which would reset time.
    if (video.readyState < 2 && originalPreload !== undefined && originalPreload !== "auto") {
      preloadChanged = true; video.preload = "auto";
    }
    // Recheck source/time after the caller's synchronous work, without a paint gate.
    queueMicrotask(verify);
  });
}
