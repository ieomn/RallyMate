import { createContext } from "react";
import type { ReplayClip } from "./lib/replay-clips";

export const ReportClipSelection = createContext<((clip: ReplayClip | null) => void) | null>(null);
