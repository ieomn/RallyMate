import type { AdviceEvidence, EvidenceStatus } from "./advice-evidence";

export type Advice = { summary: string; strengths: string[]; nextSteps: string[]; drills: Array<{ name: string; steps: string[]; durationMin: number }>; safetyNotes: string[]; confidence: "low" | "medium" | "high" };
export type AdviceResponse = { error?: string; advice?: Advice; evidenceStatus?: EvidenceStatus; evidence?: AdviceEvidence; contextStatus?: string; source?: string; providerStatus?: string };
export type ScopedAdvice = { contextKey: string; response: AdviceResponse };
export const adviceContextKey = (jobId: string | undefined, technique: string) => JSON.stringify([jobId ?? null, technique]);
export const adviceForContext = (saved: ScopedAdvice | null, contextKey: string) => saved?.contextKey === contextKey ? saved.response : null;

export function adviceNotice(payload: AdviceResponse): string {
  if (payload.source === "safety_fallback") return "以下为身体不适相关的安全提示，不是视频动作评价。";
  if (payload.source === "evidence_fallback") return "以下是识别证据说明与补充建议，未调用语言模型评价动作。";
  if (payload.source === "mimo") return "根据所选动作的已核验证据整理；未识别的部分不作评价。";
  if (payload.providerStatus === "disabled") return "语言模型建议尚未启用；以下为服务端证据说明与基础提示。";
  if (payload.providerStatus === "billing_configuration_required" || payload.providerStatus === "invalid_configuration") return "语言模型建议的接入配置尚未就绪；以下为证据说明，不是模型生成的个性化评价。";
  if (payload.providerStatus === "not_configured") return "语言模型建议服务未配置；以下为证据说明与基础提示。";
  return "语言模型建议暂不可用；已显示证据说明与基础提示，未将服务故障解释为动作问题。";
}
