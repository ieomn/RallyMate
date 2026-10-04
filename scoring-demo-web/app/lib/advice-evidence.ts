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

/** Capture suggestions request better evidence; they do not diagnose why inference failed. */
function captureSteps(family: string): string[] {
  return [
    family === "return"
      ? "如需补充接发证据，请保留对手发球、来球和接球者挥拍的连续画面。"
      : "如需补充动作证据，请提供从准备到随挥结束的连续片段，尽量让全身、球拍和球入镜。",
    "可先回看目标动作并标记时间点；补证建议不是对本次未识别原因的判断。",
  ];
}

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
    limitations: ["没有可用分析不代表没有发生该动作，也不代表动作有问题。", "文字描述属于用户自述，不能代替视频识别证据。"],
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
      explanation: `当前可复核${technique}的候选事件；只有明确记录的阶段可作阶段说明，不能据此推断动作类型准确或完整动作质量。`,
      availableFacts: unique(observed.map(row => `已关联候选事件：${trustedText(row.name_zh, 80)}（待回放复核）。`)).slice(0, 6),
      observedPhases: phases,
      limitations: ["候选事件尚待复核，不等于确认球拍触球；动作类型与离地落地也待核实，不说明技术正确、稳定或得分。", "未提供明确证据的阶段与动作原因暂不评价。"],
      nextSteps: ["结合回放复核已记录的动作事件，只比较有证据支持的部分。"],
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
    if (phases.length) facts.push(`部分片段可根据二维运动估计以下阶段：${phases.join("、")}；边界需要回放复核。`);
    if (partial) facts.push("至少一个动作片段的阶段证据不完整，不能作为完整的准备至随挥分析。");
    return {
      ...common, status: "motion_only", reasonCode: partial ? "partial_motion" : "motion_without_confirmed_contact", partialMotion: partial,
      explanation: `当前仅有${technique}相关的二维挥拍运动证据${partial ? "，部分动作阶段缺失" : ""}；可以说明测到的运动，但不能判断真实触球、完整技术质量或动作错误原因。`,
      availableFacts: facts, observedPhases: phases, missingPhases: missing.map(phase => PHASES[phase]),
      limitations: ["动作类型和阶段由姿态与球拍时序规则推断；运动峰值不是触球时刻。", "二维指标受拍摄角度影响，不等同于实际球速、三维转体角或技术评分。", "尚未确认球拍触球，不能据此计算确认击球次数。", ...(partial ? ["缺少的阶段不作质量评价，也不补写成已识别。"] : [])],
      nextSteps: ["先定位相关片段回看，核对动作类型及估计阶段是否对应实际画面。", ...captureSteps(family).slice(0, 1)],
    };
  }
  if (!rows.length && !Object.keys(familySummary).length && !Object.keys(motionFamily).length) return unavailableAdviceEvidence(technique, "analysis_unavailable", "artifact_unavailable");
  const reason = trustedText(motionFamily.reason_zh) ?? trustedText(familySummary.recognition_reason_zh);
  return {
    ...common, status: "insufficient_evidence", reasonCode: "requested_family_not_recognized",
    explanation: `当前视频尚未得到可用于评价${technique}的有效识别，无法判断该动作是否正确或为何失误。未识别不代表没有该动作，也不代表动作有问题。`,
    availableFacts: [], observedPhases: [],
    limitations: [...(reason ? [`分析系统记录：${reason}`] : []), "其他动作的识别结果或分数不能作为该动作的证据。", "尚不能确定未识别的具体原因；下方内容仅用于补充证据，不是动作纠错结论。"],
    nextSteps: captureSteps(family),
  };
}

export function mayExplainEvidence(evidence: AdviceEvidence): boolean {
  return ["motion_only", "observed"].includes(evidence.status) && evidence.availableFacts.length > 0;
}
