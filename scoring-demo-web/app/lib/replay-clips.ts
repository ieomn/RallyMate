import type { DemoResultResponse, MotionAnalysis } from "./api-types";
import { reportMoments } from "./training-report";
import { tennisAssessmentOf } from "./tennis-assessment";

export type ReplayAnchor = { id: string; label: string; sourceMs: number };
export type ReplayClip = {
  id: string; label: string; kind: "motion" | "footwork";
  startMs: number; endMs: number; anchors: ReplayAnchor[];
};
export type ReplayRange = { startMs: number; endMs: number };
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const validRange = (start: unknown, end: unknown): boolean => finite(start) && finite(end) && start >= 0 && end > start;
const phaseNames: Record<string, string> = { preparation: "准备", acceleration: "加速挥拍", swing: "挥拍", follow_through: "随挥", recovery: "恢复", loading: "蓄势" };

/** Retain source boundaries; context is added only when a particular video is played. */
export function replayClipsOf(result: DemoResultResponse | null, motion: MotionAnalysis | null): ReplayClip[] {
  const assessment = tennisAssessmentOf(result);
  if (assessment?.episodes !== undefined) return assessment.episodes.map(episode => ({
    id: `tennis:${episode.episode_id}`, label: episode.family === "serve" ? "发球动作" : episode.family === "return" ? "接发动作" : "挥拍动作",
    kind: "motion", startMs: episode.start_ms, endMs: episode.end_ms,
    anchors: [
      { id: "start", label: "动作开始", sourceMs: episode.start_ms },
      { id: "peak", label: "运动峰值", sourceMs: episode.peak_ms },
      { id: "end", label: "动作结束", sourceMs: episode.end_ms },
    ],
  }));
  const clips: ReplayClip[] = [];
  const seen = new Set<string>();
  const add = (clip: ReplayClip) => {
    const key = `${clip.kind}:${clip.startMs}:${clip.endMs}`;
    if (!validRange(clip.startMs, clip.endMs) || seen.has(key) || clips.some(row => row.id === clip.id)) return;
    seen.add(key);
    const times = new Set<number>();
    clip.anchors = clip.anchors.filter(anchor => finite(anchor.sourceMs) && anchor.sourceMs >= clip.startMs && anchor.sourceMs <= clip.endMs)
      .sort((a, b) => a.sourceMs - b.sourceMs)
      .filter(anchor => !times.has(anchor.sourceMs) && Boolean(times.add(anchor.sourceMs)));
    clips.push(clip);
  };
  for (const family of Object.values(motion?.families ?? {})) for (const episode of family?.episodes ?? []) {
    if (!validRange(episode.start_ms, episode.end_ms) || !finite(episode.peak_ms)
      || episode.peak_ms < episode.start_ms || episode.peak_ms > episode.end_ms) continue;
    const anchors: ReplayAnchor[] = [
      { id: "start", label: "动作开始", sourceMs: episode.start_ms },
      { id: "peak", label: "运动峰值", sourceMs: episode.peak_ms },
      { id: "end", label: "动作结束", sourceMs: episode.end_ms },
    ];
    for (const phase of episode.phases ?? []) {
      if (phase.status !== "measured" || !validRange(phase.start_ms, phase.end_ms)
        || phase.start_ms! < episode.start_ms || phase.end_ms! > episode.end_ms) continue;
      const label = phaseNames[phase.phase];
      if (!label) continue;
      anchors.push({ id: `${phase.phase}-start`, label: `${label}开始`, sourceMs: phase.start_ms! },
        { id: `${phase.phase}-end`, label: `${label}结束`, sourceMs: phase.end_ms! });
    }
    add({ id: `motion:${episode.episode_id}`, label: episode.family === "serve" ? "发球动作" : episode.family === "return" ? "接发动作" : "挥拍动作",
      kind: "motion", startMs: episode.start_ms, endMs: episode.end_ms, anchors });
  }
  // Rotation moments describe the episodes above. Only independent footwork
  // windows add another clip, never a second copy of the same swing.
  for (const moment of reportMoments(result, motion)) if (moment.kind === "footwork") {
    add({ id: moment.id, label: "步伐动作", kind: "footwork", startMs: moment.startMs, endMs: moment.endMs,
      anchors: [{ id: "start", label: "步伐开始", sourceMs: moment.startMs }, { id: "end", label: "步伐结束", sourceMs: moment.endMs }] });
  }
  return clips.sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs || a.id.localeCompare(b.id));
}

export function replayRangeForMedia(clip: ReplayClip, originMs: number, durationSeconds: number): ReplayRange | null {
  if (!validRange(clip.startMs, clip.endMs) || !finite(originMs) || originMs < 0 || !finite(durationSeconds) || durationSeconds <= 0) return null;
  const end = originMs + durationSeconds * 1000;
  // Do not silently trim an evidence window to make it fit a shorter export.
  if (clip.startMs < originMs || clip.endMs > end) return null;
  return { startMs: Math.max(originMs, clip.startMs - 350), endMs: Math.min(end, clip.endMs + 350) };
}

export type ReplayMedia = Pick<HTMLVideoElement, "currentTime" | "duration" | "paused" | "readyState" | "seeking" | "seekable" | "playbackRate" | "play" | "pause" | "addEventListener" | "removeEventListener">;
export type ClipPlaybackState = { ready: boolean; originMs: number; durationSeconds: number; sourceMs: number; selectedId: string | null; looping: boolean; notice: string };
export const emptyClipPlayback: ClipPlaybackState = { ready: false, originMs: 0, durationSeconds: 0, sourceMs: 0, selectedId: null, looping: false, notice: "" };

/** Event-driven seeking: a request waits for metadata and an actual seekable range. */
export function createReplayClipController(update: (state: ClipPlaybackState) => void) {
  let media: ReplayMedia | null = null, origin = 0, sourceKey = "";
  let clip: ReplayClip | null = null, looping = false, notice = "";
  let pending: { sourceMs: number; play: boolean } | null = null;
  let last: { sourceMs: number; play: boolean; rate: number } | null = null;
  let explicitSwitchTarget: { sourceMs: number; play: boolean } | null = null;
  let awaitingMetadata = false, attachment = 0;
  let metadataRestore: { sourceMs: number; play: boolean; rate: number } | null = null;
  let remove = () => {};
  const ready = () => !!media && media.readyState >= 1 && finite(media.duration) && media.duration > 0;
  const sourceTime = () => media && finite(media.currentTime) ? origin + media.currentTime * 1000 : last?.sourceMs ?? 0;
  const emit = () => update({ ready: ready(), originMs: origin, durationSeconds: ready() ? media!.duration : 0,
    sourceMs: sourceTime(), selectedId: clip?.id ?? null, looping, notice });
  const save = () => { if (media && ready() && !pending && !awaitingMetadata) last = { sourceMs: sourceTime(), play: !media.paused, rate: media.playbackRate }; };
  const flush = () => {
    if (!media || !pending || !ready() || awaitingMetadata) return;
    const seconds = (pending.sourceMs - origin) / 1000;
    if (!finite(seconds) || seconds < 0 || seconds > media.duration) {
      pending = null; notice = "当前视频不包含这个时间点，请切换原视频查看。"; emit(); return;
    }
    if (!Array.from({ length: media.seekable.length }, (_, index) => index).some(index => seconds >= media!.seekable.start(index) && seconds <= media!.seekable.end(index))) {
      notice = "正在读取所选时间点的视频…"; emit(); return;
    }
    const request = pending, current = media;
    pending = null; notice = "";
    current.pause();
    current.currentTime = seconds;
    if (request.play) void current.play().catch(() => { if (media === current) { notice = "视频已定位，请按播放继续。"; emit(); } });
    last = { sourceMs: request.sourceMs, play: request.play, rate: current.playbackRate };
    emit();
  };
  const seek = (sourceMs: number, play = false, preserveOnSwitch = false) => {
    if (!finite(sourceMs) || sourceMs < 0) return false;
    if (preserveOnSwitch) explicitSwitchTarget = { sourceMs, play };
    pending = { sourceMs, play };
    if (metadataRestore || awaitingMetadata) metadataRestore = { sourceMs, play, rate: metadataRestore?.rate ?? media?.playbackRate ?? last?.rate ?? 1 };
    flush(); return true;
  };
  const onProgress = (ended = false) => {
    flush();
    if (media && ready() && clip && !media.seeking && (!media.paused || ended) && !pending && !awaitingMetadata) {
      const range = replayRangeForMedia(clip, origin, media.duration);
      if (range && sourceTime() >= range.endMs) {
        if (looping) seek(range.startMs, true);
        else { media.pause(); seek(range.endMs, false); }
      }
    }
    save(); emit();
  };
  return {
    attach(next: ReplayMedia | null, originMs = 0, key = "") {
      if (media === next && sourceKey === key && origin === originMs) return;
      if (media !== next || sourceKey === key) save();
      remove(); media = null; const generation = ++attachment;
      if (!next || !finite(originMs) || originMs < 0) { emit(); return; }
      media = next; origin = originMs; sourceKey = key;
      if (last) next.playbackRate = last.rate;
      pending = explicitSwitchTarget ?? pending ?? (last ? { sourceMs: last.sourceMs, play: last.play } : null);
      metadataRestore = pending ? { ...pending, rate: last?.rate ?? next.playbackRate } : null;
      awaitingMetadata = next.readyState < 1;
      explicitSwitchTarget = null;
      const metadata = (event?: Event) => {
        if (clip && ready() && !replayRangeForMedia(clip, origin, next.duration)) { clip = null; looping = false; }
        // durationchange may expose a seekable range before loadedmetadata.
        // Keep the transfer target until React's view-local metadata handler
        // has finished restoring its default time/rate, even if an eager
        // cached-media event already consumed the ordinary pending seek.
        if (event?.type === "loadedmetadata") {
          awaitingMetadata = true;
          queueMicrotask(() => {
            if (media !== next || sourceKey !== key || generation !== attachment) return;
            const restore = metadataRestore;
            awaitingMetadata = false; metadataRestore = null;
            if (restore) {
              next.playbackRate = restore.rate;
              pending ??= { sourceMs: restore.sourceMs, play: restore.play };
            }
            onProgress();
          });
        }
        else onProgress(event?.type === "ended");
      };
      const events = ["loadedmetadata", "loadeddata", "durationchange", "canplay", "progress", "timeupdate", "seeked", "play", "pause", "ended"];
      for (const name of events) next.addEventListener(name, metadata);
      remove = () => { for (const name of events) next.removeEventListener(name, metadata); };
      metadata();
    },
    select(next: ReplayClip | null) {
      clip = next; looping = next ? looping : false; notice = "";
      if (next && media && ready()) {
        const range = replayRangeForMedia(next, origin, media.duration);
        if (!range) { clip = null; looping = false; notice = "当前视频未包含这段完整动作，请切换原视频查看。"; emit(); return; }
        seek(range.startMs, true);
      }
      emit();
    },
    seek,
    restart() {
      if (!media || !ready()) return;
      const range = clip ? replayRangeForMedia(clip, origin, media.duration) : null;
      seek(range?.startMs ?? origin, true);
    },
    setLoop(value: boolean) { looping = !!clip && value; emit(); },
    dispose() { save(); remove(); media = null; attachment++; metadataRestore = null; awaitingMetadata = false; },
  };
}
