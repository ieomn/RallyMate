/** Presentation only: preserve the report, source bindings and calculation contracts. */
export function productText(value: unknown): string {
  if (typeof value !== "string") return "";
  return value
    .replace(/原文\s*P0080[^。]*。/gu, "观察准备时段的头部移动速度和朝向变化；不能据此确认实际视线、是否盯球或启动时机。")
    .replace(/[（(][^（）()\n]*(?:CARD-(?:FS|GS):|word\/document\.xml|P\d{4,6}|\.(?:docx?|pdf|xlsx?))[^（）()\n]*[）)]/giu, "")
    .replace(/CARD-(?:FS|GS):word\/document\.xml:P\d{4,6}/gu, "")
    .replace(/(?:《[^》\n]*\.(?:docx?|pdf|xlsx?)》|[^\s，。；：<>"“”]+\.(?:docx?|pdf|xlsx?))/giu, "")
    .replace(/(?:正文第\s*\d+\s*段|\bP\d{4,6}\b|\bT\d{3}(?:\.R\d+\.C\d+)?\b)/gu, "")
    .replace(/Word\s*(?:标准|评分卡|指标)/giu, "技术指标")
    .replace(/Word/giu, "技术")
    .replace(/原文(?:段落|位置|来源)[：:]?/gu, "")
    .replace(/原文动作条件/gu, "动作条件")
    .replace(/原文测量/gu, "动作测量")
    .replace(/原文(?:要求|标准|条件)/gu, "动作要求")
    .replace(/原文(?:指标|卡)/gu, "技术指标")
    .replace(/原文/gu, "动作要求")
    .replace(/候选事件尚待复核|候选动作待复核|动作类型待复核/gu, "动作类型尚未确认")
    .replace(/候选片段/gu, "运动片段")
    .replace(/[（(]\s*[）)]/gu, "")
    .replace(/[ \t]{2,}/gu, " ")
    .trim();
}
