export type AdviceEvidenceStatus = "no_video" | "analysis_unavailable" | "insufficient_evidence" | "motion_only" | "observed";
export type EvidenceStatus = AdviceEvidenceStatus;
export type AdviceEvidence = {
  status: AdviceEvidenceStatus;
  technique: string;
  family: string;
  reasonCode: string;
  explanation: string;
  availableFacts: string[];
  limitations: string[];
  nextSteps: string[];
  observedPhases: string[];
  missingPhases: string[];
  partialMotion: boolean;
};

const FAMILIES: Record<string, string> = { "底线击球": "baseline", "发球": "serve", "接发": "return", "网前进攻": "net_attack", "步伐": "footwork" };
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const unique = (items: string[]) => [...new Set(items)];
const PHASES: Record<string, string> = {
  preparation: "准备", acceleration: "加速挥拍", follow_through: "随挥",
  observation: "观察", compact_preparation: "紧凑准备", positioning: "到位", strike: "挥拍", recovery: "回位",
  setup: "准备站位", toss: "抛球", loading: "蓄力", upward_swing: "向上挥拍", contact: "触球阶段", landing: "落地",
  ready: "准备", split_step: "分腿垫步", initiation: "启动", movement: "移动", adjustment: "调整", support: "支撑",
};
const MOTION_PHASES = ["preparation", "acceleration", "follow_through"];
const unsafeText = /(ignore\s+(all\s+)?previous|system\s*prompt|developer\s*message|api[-_ ]?key|secret|token|越过提示|忽略(之前|上面)|系统提示|开发者消息|密钥|https?:\/\/|[<>]|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\b1[3-9]\d{9}\b)/i;
function trustedText(value: unknown, max = 220): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  // eslint-disable-next-line no-control-regex
  return text && text.length <= max && !unsafeText.test(text) && !/[\u0000-\u001f\u007f]/.test(text) ? text : null;
}

/** A baseline practice remains useful when no individual technical grade exists. */
function skillSteps(family: string): string[] {
  if (family === "serve") return ["先不挥拍，做 2 组、每组 5 次舒适抛球，组间休息，再衔接轻松挥拍。", "同一站位复测 10 次，记录无需追球即可接上挥拍的次数。"];
  if (family === "footwork") return ["从准备标记出发，接近距离慢球后轻击，再用小步回位，做 2 组、每组 5 球。", "按同样喂球节奏复测 10 球，记录下一球发出前已回位的次数。"];
  return ["用熟悉的击球方式在小场地接低速球，轻送到大目标区，做 2 组、每组 5 球，组间休息。", "保持来球与目标相同，再做 10 球，记录进入目标区的次数。"];
}
function captureSteps(family: string): string[] {
  return [
    family === "return" ? "下次保留对手发球、来球和接球者挥拍的连续画面。"
      : "下次固定机位，保留准备到恢复的完整片段，让全身与球拍持续入镜。",
  ];
}
const actionName = (value: unknown) => (trustedText(value, 80) ?? "动作").replace(/(?:原文|Word|P\d{3,}|\b(?:FS|GS)\d{2}(?:-M\d{2})?(?:-\d{2})?\b)/giu, "").replace(/候选事件|候选|尚待复核|待复核/g, "").trim() || "动作";

export function unavailableAdviceEvidence(technique: string, status: "no_video" | "analysis_unavailable", reasonCode: string): AdviceEvidence {
  const family = FAMILIES[technique] ?? "unknown";
  const reasons: Record<string, string> = {
    no_video: `当前未关联视频分析，无法评价这次${technique}动作。`,
    job_mismatch: `分析结果与当前任务不匹配，无法评价这次${technique}动作。`,
    job_not_ready: `当前任务尚未提供完成的分析结果，暂时无法评价这次${technique}动作。`,
    job_not_found: `未找到当前视频任务，无法读取${technique}分析。`,
    artifact_unavailable: `当前任务未提供可核验的${technique}分析数据，暂时无法评价该动作。`,
    analysis_unavailable: `当前无法读取${technique}分析，暂时无法评价该动作。`,
  };
  return {
    status, technique, family, reasonCode,
    explanation: reasons[reasonCode] ?? reasons.analysis_unavailable,
    availableFacts: [], observedPhases: [], missingPhases: [], partialMotion: false,
    limitations: ["当前尚无这项动作的个体测量结论。"],
    nextSteps: status === "no_video"
      ? ["先上传视频并等待分析完成，再选择对应动作查看说明。"]
      : ["确认选中了正确的视频任务，待分析完成后重试；若仍无法读取，请保留任务编号以便排查。"],
  };
}

/** Trust only the requested family; a high footwork score never certifies a stroke. */
export function adviceEvidenceFromPayload(technique: string, jobId: string, raw: unknown): AdviceEvidence {
  const payload = record(raw), family = FAMILIES[technique] ?? "unknown";
  if (payload.job_id !== jobId) return unavailableAdviceEvidence(technique, "analysis_unavailable", "job_mismatch");
  if (payload.status !== "ready") return unavailableAdviceEvidence(technique, "analysis_unavailable", "job_not_ready");
  const assessment = record(payload.technique_assessment);
  if (assessment.job_id !== undefined && assessment.job_id !== jobId) return unavailableAdviceEvidence(technique, "analysis_unavailable", "job_mismatch");
  const familySummary = record(record(assessment.family_summary)[family]);
  const rows = array(assessment.techniques).map(record).filter(row => row.family === family);
  const observed = rows.filter(row => row.observed === true && ["ready", "partial"].includes(String(row.status)) && trustedText(row.name_zh, 80));
  const common = { technique, family, partialMotion: false, missingPhases: [] as string[] };
  if (observed.length) {
    const phases = unique(observed.flatMap(row => array(row.phase_statuses).map(record).filter(phase => phase.status === "measured" && phase.evidence_source === "explicit_phase_record").map(phase => PHASES[String(phase.phase)]).filter(Boolean)));
    return {
      ...common, status: "observed", reasonCode: "family_events_observed",
      explanation: `已定位与${technique}相关的动作片段，可以结合回放选择一个练习目标。`,
      availableFacts: unique(observed.map(row => `已定位${actionName(row.name_zh)}相关片段。`)).slice(0, 6),
      observedPhases: phases,
      limitations: ["片段定位不等于确认球拍触球；本页仅说明已测量的动作部分。"],
      nextSteps: skillSteps(family),
    };
  }
  const recognition = record(payload.action_recognition ?? assessment.action_recognition);
  const motion = record(recognition.motion_analysis), motionFamily = record(record(motion.families)[family]);
  if (familySummary.recognition_status === "analysis_unavailable" || motion.status === "unavailable") return unavailableAdviceEvidence(technique, "analysis_unavailable", "artifact_unavailable");
  const validMotion = motion.schema_version === "1.0.0" && motion.contact_confirmed === false && motionFamily.status === "analyzed";
  const episodeIds = new Set<string>();
  const episodes = validMotion ? array(motionFamily.episodes).map(record).filter(episode => {
    if (episode.family !== family || episode.contact_confirmed !== false || episode.method !== "rule_based") return false;
    if (typeof episode.episode_id !== "string" || !episode.episode_id || episodeIds.has(episode.episode_id)) return false;
    if (![episode.start_ms, episode.peak_ms, episode.end_ms].every(finite) || Number(episode.start_ms) < 0 || Number(episode.start_ms) > Number(episode.peak_ms) || Number(episode.peak_ms) > Number(episode.end_ms) || Number(episode.end_ms) <= Number(episode.start_ms)) return false;
    const evidence = record(episode.evidence);
    const metrics = record(episode.metrics);
    const measured = finite(evidence.pose_samples) && evidence.pose_samples >= 3 && ["peak_wrist_speed_torso_per_s", "wrist_path_torso", "elbow_extension_deg", "shoulder_line_change_deg"].some(key => finite(metrics[key]) && Number(metrics[key]) >= 0);
    if (measured) episodeIds.add(episode.episode_id);
    return measured;
  }) : [];
  if (episodes.length) {
    const perEpisodePhases = episodes.map(episode => array(episode.phases).map(record).filter(phase => {
      return MOTION_PHASES.includes(String(phase.phase)) && phase.status === "measured" && finite(phase.start_ms) && finite(phase.end_ms) && phase.start_ms >= Number(episode.start_ms) && phase.end_ms <= Number(episode.end_ms) && phase.end_ms > phase.start_ms;
    }).map(phase => String(phase.phase)));
    const missing = unique(perEpisodePhases.flatMap(phases => MOTION_PHASES.filter(phase => !phases.includes(phase))));
    const partial = missing.length > 0 || episodes.some(episode => episode.analysis_status === "partial");
    const phases = unique(perEpisodePhases.flat()).map(phase => PHASES[phase]);
    const facts = [`已获得${episodes.length}段${technique}相关的二维运动测量。`];
    if (phases.length) facts.push(`本次测量覆盖${phases.join("、")}阶段。`);
    if (partial) facts.push(`部分片段还缺少${missing.map(phase => PHASES[phase]).join("、")}阶段的测量。`);
    return {
      ...common, status: "motion_only", reasonCode: partial ? "partial_motion" : "motion_without_confirmed_contact", partialMotion: partial,
      explanation: `已取得${technique}的画面运动测量${partial ? "，部分动作阶段尚未覆盖" : ""}。先练一个固定任务，再比较下一次的完成情况。`,
      availableFacts: facts, observedPhases: phases, missingPhases: missing.map(phase => PHASES[phase]),
      limitations: ["画面运动受拍摄角度影响；运动峰值不是触球时刻，也不代表真实受力。"],
      nextSteps: skillSteps(family),
    };
  }
  if (!rows.length && !Object.keys(familySummary).length && !Object.keys(motionFamily).length) return unavailableAdviceEvidence(technique, "analysis_unavailable", "artifact_unavailable");
  return {
    ...common, status: "insufficient_evidence", reasonCode: "requested_family_not_recognized",
    explanation: `本次没有足够的${technique}测量来制定个体纠正方案。可以先完成下面的基础练习，建立自己的记录。`,
    availableFacts: [], observedPhases: [],
    limitations: ["基础练习不使用其他动作的分数，也不把缺少测量当作技术缺陷。"],
    nextSteps: [...skillSteps(family), ...captureSteps(family)],
  };
}

export function mayExplainEvidence(evidence: AdviceEvidence): boolean {
  return ["motion_only", "observed"].includes(evidence.status) && evidence.availableFacts.length > 0;
}
