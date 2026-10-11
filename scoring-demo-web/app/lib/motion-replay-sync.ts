export type ReplayVideoState = "loading" | "ready" | "buffering" | "ended" | "unavailable";
export type ReplaySyncSnapshot = { elapsed: number; duration: number; playing: boolean; buffering: boolean; rate: number; videos: Record<string, ReplayVideoState> };
type ReplayMedia = Pick<HTMLVideoElement, "duration" | "currentTime" | "readyState" | "seeking" | "paused" | "playbackRate" | "play" | "pause" | "load">;
export type ReplaySyncEntry = { id: string; video: ReplayMedia };

/** All sources share elapsed seconds; their different durations are never stretched. */
export function replayVideoTime(elapsed: number, duration: number): number {
  return Math.min(Math.max(0, elapsed), Number.isFinite(duration) && duration > 0 ? duration : 0);
}

export class MotionReplaySynchronizer {
  private elapsed = 0;
  private rate = 1;
  private intended = false;
  private running = false;
  private lastNow: number | null = null;
  private resumedAt = 0;
  private failed = new Set<string>();
  private waitingSince = new Map<string, number>();

  constructor(private entries: ReplaySyncEntry[], private expectedDuration: number) {}

  private duration() {
    return Math.max(this.expectedDuration, ...this.entries.map(({ video }) => Number.isFinite(video.duration) ? video.duration : 0));
  }

  private stopMedia() {
    this.entries.forEach(({ video }) => video.pause());
    this.running = false;
    this.lastNow = null;
  }

  play() {
    if (this.elapsed >= this.duration() - 0.005) this.seek(0);
    this.intended = true;
  }

  pause() { this.intended = false; this.stopMedia(); }

  seek(seconds: number) {
    this.pause();
    this.elapsed = Math.min(this.duration(), Math.max(0, seconds));
    this.entries.forEach(({ video }) => {
      if (Number.isFinite(video.duration) && video.duration > 0) video.currentTime = replayVideoTime(this.elapsed, video.duration);
    });
  }

  step(seconds: number) { this.seek(this.elapsed + seconds); }

  setRate(rate: number) {
    this.rate = rate;
    this.entries.forEach(({ video }) => { video.playbackRate = rate; });
  }

  fail(id: string) { this.failed.add(id); this.waitingSince.delete(id); this.entries.find(entry => entry.id === id)?.video.pause(); }

  retry(id: string) {
    this.failed.delete(id);
    this.waitingSince.delete(id);
    this.entries.find(entry => entry.id === id)?.video.load();
  }

  tick(now: number): ReplaySyncSnapshot {
    const duration = this.duration();
    if (this.running && this.lastNow !== null) this.elapsed = Math.min(duration, this.elapsed + Math.max(0, now - this.lastNow) / 1000 * this.rate);
    this.lastNow = this.running ? now : null;
    if (this.elapsed >= duration - 0.005) this.pause();
    const states: Record<string, ReplayVideoState> = {};
    let blocked = false;
    let available = 0;

    for (const { id, video } of this.entries) {
      if (this.failed.has(id)) { states[id] = "unavailable"; continue; }
      const known = Number.isFinite(video.duration) && video.duration > 0;
      const ended = known && this.elapsed >= video.duration - 0.005;
      const target = replayVideoTime(this.elapsed, video.duration);
      // Correct material drift only after playback has settled. Seek all other videos
      // into the same paused clock, rather than chasing a moving master.
      if (known && !video.seeking && Math.abs(video.currentTime - target) > 0.12 && (!this.running || now - this.resumedAt > 400)) video.currentTime = target;
      const waiting = !known || video.seeking || (!ended && video.readyState < 3);
      if (waiting) {
        if (!this.waitingSince.has(id)) this.waitingSince.set(id, now);
        if (now - this.waitingSince.get(id)! >= 10000) {
          this.fail(id); video.pause(); states[id] = "unavailable"; continue;
        }
        states[id] = known ? "buffering" : "loading";
        blocked = true;
      } else {
        this.waitingSince.delete(id);
        states[id] = ended ? "ended" : "ready";
      }
      if (!ended) available++;
      if (ended) {
        video.pause();
        if (!video.seeking && Math.abs(video.currentTime - video.duration) > 0.001) video.currentTime = video.duration;
      }
    }

    if (blocked || !this.intended || available === 0) this.stopMedia();
    else {
      if (!this.running) { this.running = true; this.lastNow = now; this.resumedAt = now; }
      for (const { id, video } of this.entries) {
        if (states[id] === "ready" && video.paused) {
          video.playbackRate = this.rate;
          void video.play().catch(error => {
            if (error?.name !== "AbortError") this.fail(id);
          });
        }
      }
    }
    if (available === 0) this.intended = false;
    return { elapsed: this.elapsed, duration, playing: this.intended, buffering: this.intended && blocked, rate: this.rate, videos: states };
  }
}
