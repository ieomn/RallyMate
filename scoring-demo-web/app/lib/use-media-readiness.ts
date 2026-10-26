"use client";

import { useEffect, useState, type RefObject } from "react";
import { observeMediaReadiness, type MediaReadiness } from "./media-readiness";

export function useMediaReadiness(media: RefObject<HTMLVideoElement | null>, sourceKey: string) {
  const [attempt, setAttempt] = useState(0);
  const key = `${sourceKey}:${attempt}`;
  const [observed, setObserved] = useState<{ key: string; state: MediaReadiness } | null>(null);
  useEffect(() => {
    if (!media.current) return;
    return observeMediaReadiness(media.current, state => setObserved({ key, state }));
  }, [media, key]);
  return { key, state: observed?.key === key ? observed.state : "loading" as MediaReadiness, retry: () => setAttempt(value => value + 1) };
}
