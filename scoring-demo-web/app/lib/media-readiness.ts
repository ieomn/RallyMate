export type MediaReadiness = "loading" | "ready" | "failed" | "timeout";
export type ObservableMedia = Pick<HTMLMediaElement, "readyState" | "error" | "paused" | "addEventListener" | "removeEventListener">;

/** A stalled media request must leave a visible retry path, without changing the media. */
export function observeMediaReadiness(media: ObservableMedia, update: (state: MediaReadiness) => void, timeoutMs = 20000): () => void {
  let disposed = false;
  let settledFailure = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const clear = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };
  const emit = (state: MediaReadiness) => { if (!disposed) update(state); };
  const ready = () => { clear(); settledFailure = false; emit("ready"); };
  const failed = () => { clear(); settledFailure = true; emit("failed"); };
  const loading = () => {
    if (disposed || settledFailure || timer !== undefined) return;
    emit("loading");
    timer = setTimeout(() => { timer = undefined; settledFailure = true; emit("timeout"); }, timeoutMs);
  };
  const start = () => { clear(); settledFailure = false; loading(); };
  const metadata = () => { if (media.readyState >= 1) ready(); };
  const progress = () => { if (media.readyState >= 3) ready(); };
  // Metadata-only preloading may stall normally while the user has paused.
  const buffering = () => { if (media.readyState < 1 || !media.paused) loading(); };
  const listeners: Array<[string, () => void]> = [
    ["loadedmetadata", metadata], ["canplay", ready], ["playing", ready], ["progress", progress],
    ["loadstart", start], ["waiting", buffering], ["stalled", buffering], ["pause", metadata], ["error", failed],
  ];
  for (const [name, listener] of listeners) media.addEventListener(name, listener);
  if (media.error) failed(); else if (media.readyState >= 1) ready(); else loading();
  return () => { disposed = true; clear(); for (const [name, listener] of listeners) media.removeEventListener(name, listener); };
}
