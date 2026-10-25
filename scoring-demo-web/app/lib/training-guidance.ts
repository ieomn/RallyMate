import type { DemoResultResponse, MotionAnalysis } from "./api-types";
import { indicatorAssessmentOf, type IndicatorObservation } from "./indicator-assessment";
import { reportFocus, type ReportFocus } from "./training-report";
import { reportVideoDurationMs } from "./workspace-navigation";
import { cardExecutionOf, type CardCondition } from "./card-execution";
import { isFormalScoringPolicy } from "./formal-card-scoring-policy";

const FEATURE_LABELS: Record<string, string> = {
  head_center_speed_body_s: "头部移动速度", head_center_y_body: "头部高度", head_center_vertical_velocity_body_s: "头部上下移动速度",
  hip_drop_torso: "髋部下降幅度", body_speed_std_torso_s: "身体移动速度波动", hip_inside_support_fraction: "髋投影位于双脚之间的比例",
  supported_low_motion_duration_ms: "减速后的支撑时长", successor_interval_ms: "衔接下一动作的间隔",
  hip_speed_drop_torso_s: "髋部速度下降量", hip_speed_drop_fraction: "髋部速度下降比例", trunk_tilt_std_deg: "躯干倾角波动",
  shoulder_hip_angular_speed_drop_deg_s: "肩髋转动减速量",
  bilateral_foot_rise_min_body: "双脚共同上升幅度", bilateral_foot_rise_proxy_duration_ms: "双脚上升候选持续时间",
  bilateral_foot_rise_synchrony_ms: "双脚上升时间差", bilateral_foot_vertical_slowdown_time_offset_ms: "双脚垂直减速时间差",
  body_center_speed_body_s: "身体中心移动速度", braking_ankle_slowdown_to_hip_deceleration_ms: "制动脚减速到髋部减速的间隔",
  braking_ankle_speed_drop_body_s: "制动脚速度下降量", braking_side_code: "制动侧标记", double_support_proxy_duration_ms: "双脚支撑候选持续时间",
  drive_side_code: "推动侧标记", first_step_displacement_body: "第一步位移", hip_acceleration_along_launch_direction_body_s2: "髋部沿启动方向的加速度",
  hip_center_deceleration_body_s2: "髋中心减速度", hip_center_lateral_variability_body: "髋中心横向波动",
  hip_center_motion_direction_deg: "髋中心移动方向", hip_center_relative_to_ankle_midpoint_x_body: "髋中心相对双踝中点的横向偏移",
  hip_center_relative_to_ankle_midpoint_y_body: "髋中心相对双踝中点的纵向偏移", hip_center_relative_to_ankle_support: "髋中心相对双踝支撑的位置比例",
  hip_center_speed_body_s: "髋中心移动速度", hip_center_speed_drop_body_s: "髋中心速度下降量", hip_center_speed_trend_body_s2: "髋中心速度变化率",
  hip_center_vertical_velocity_body_s: "髋中心垂直移动速度", hip_center_y_body: "髋中心纵向位置", hip_deceleration_to_double_support_proxy_ms: "髋部减速到双脚支撑候选的间隔",
  hip_height_delta_body: "髋高度变化", launch_direction_deg: "启动方向", launch_foot_motion_duration_ms: "启动脚移动持续时间",
  launch_foot_relative_displacement_body: "启动脚相对位移", launch_foot_slowdown_to_post_hip_direction_ms: "启动脚减速到髋部方向确认的间隔",
  launch_foot_speed_drop_body_s: "启动脚速度下降量", launch_foot_speed_peak_body_s: "启动脚速度峰值", launch_side_code: "启动侧标记",
  left_ankle_speed_body_s: "左踝移动速度", left_ankle_speed_drop_body_s: "左踝速度下降量", left_knee_flexion_change_deg: "左膝屈曲变化",
  right_ankle_speed_body_s: "右踝移动速度", right_ankle_speed_drop_body_s: "右踝速度下降量", right_knee_flexion_change_deg: "右膝屈曲变化",
  post_slowdown_stance_width_body: "减速后站距", post_step_hip_direction_consistency: "迈步后髋部方向一致性", post_step_stance_width_body: "迈步后站距",
  shoulder_hip_angular_velocity: "肩髋相对角速度", shoulder_hip_angular_velocity_change_deg_s: "肩髋相对角速度变化",
  stability_duration_ms: "稳定候选持续时间", stance_width_body: "双脚站距", support_drive_to_moving_foot_rise_proxy_ms: "支撑推动到移动脚上升候选的间隔",
  support_knee_extension_velocity_deg_s: "支撑腿伸膝角速度", torso_lean_deg: "躯干投影倾角", torso_lean_variability_deg: "躯干倾角波动",
  ankle_distance_over_hip_width: "踝距与髋宽比", head_to_shoulders_distance_torso: "头部相对双肩距离", hip_axis_deg: "髋线投影角",
  hip_inside_ankle_support_fraction: "髋投影处于双踝支撑区间的比例", hip_to_ankle_midpoint_torso: "髋中心相对双踝中点偏移",
  landing_proxy_to_next_fs02_ms: "落地候选到后续启动间隔", left_elbow_angle_deg: "左肘夹角", left_knee_flexion_deg: "左膝屈曲角",
  left_preload_knee_flexion_change_deg: "左膝预加载屈曲变化", post_landing_body_speed_std_body_s: "落地候选后速度波动",
  post_slowdown_ankle_width_to_hip_width_ratio: "减速后踝距与髋宽比", preload_body_speed_change_rate_body_s2: "预加载速度变化率",
  preload_hip_height_drop_body: "预加载髋高度下降量", right_elbow_angle_deg: "右肘夹角", right_knee_flexion_deg: "右膝屈曲角",
  right_preload_knee_flexion_change_deg: "右膝预加载屈曲变化", shoulder_axis_deg: "肩线投影角", shoulder_hip_separation_deg: "肩髋投影分离角",
  stable_control_body_speed_std_body_s: "稳定候选后速度波动", stable_control_proxy_to_next_fs10_or_fs02_ms: "稳定候选到后续动作间隔",
  trunk_tilt_deg: "躯干投影倾角", wrist_separation_torso: "双腕距离",
};
const UNIT_LABELS: Record<string, string> = { body: "身体尺度", "body/s": "身体尺度/秒", "body/s2": "身体尺度/秒²", torso: "躯干长度", "torso/s": "躯干长度/秒", deg: "度", "deg/s": "度/秒", ms: "毫秒", s: "秒", ratio: "倍", fraction: "（比例）", count: "次", code: "（侧别标记）", "%": "%" };
const publicLabel = (value: string) => value.replace(/(?:原文|Word|P\d{3,}|\b(?:FS|GS)\d{2}(?:-M\d{2})?(?:-\d{2})?\b)/giu, "").replace(/候选(?:前段|后段)?|尚待复核|待原片复核/g, "").trim();
const guidanceLabel = (feature: string, label: string) => publicLabel(FEATURE_LABELS[feature] ?? (/[\u3400-\u9fff]/u.test(label) && !/[a-z]+_[a-z0-9_]+/iu.test(label) ? label : "动作测量值"));
const guidanceUnit = (unit: string) => UNIT_LABELS[unit] ?? (/[\u3400-\u9fff]/u.test(unit) ? unit : unit ? "（单位见指标详情）" : "");

export type TrainingFocusSelection = { title: string; question: string; timestampMs?: number };
export type TrainingGuidance = ReportFocus & {
  evidenceState: "observation" | "review" | "unavailable";
  observation: string;
  question: string;
  goal: string;
  drill: string;
  retest: string;
  nextStep: string;
  limitation: string;
  source_links?: Array<{ title: string; url: string }>;
};

type PracticeGroup = "head" | "posture" | "movement" | "balance" | "rotation" | "swing" | "general";
type PracticePlan = Pick<TrainingGuidance, "title" | "goal" | "drill" | "retest" | "nextStep" | "source_links">;
// Sources are allowlisted; imported report content never supplies links or drills.
const PRACTICE_SOURCES = {
  general: { title: "USTA 成人入门练习", url: "https://www.usta.com/content/dam/usta/content-fragments/tennis-service-center/assets/pdfs/USTA-Tennis-101-for-Adults.pdf" },
  footwork: { title: "LTA 移动与回位练习", url: "https://www.lta.org.uk/play/tennis-tips-and-techniques/improve-your-tennis-footwork-on-court-with-these-simple-tips/" },
  forehand: { title: "USTA 正手示范", url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--forehand.html" },
  backhand: { title: "USTA 反手示范", url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--backhand.html" },
  serve: { title: "LTA 发球抛球练习", url: "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-serve-for-beginners/" },
} satisfies Record<string, { title: string; url: string }>;

/** Classify anatomy before motion words: head speed must never select an arm drill. */
function practiceGroup(feature: string, footwork: boolean): PracticeGroup {
  if (/(?:^|_)head(?:_|$)/.test(feature)) return "head";
  if (/knee|hip.*(?:height|_y_|vertical|drop|reference_dy)|preload.*hip/.test(feature)) return "posture";
  if (/stance|ankle.*(?:width|distance)|support|hip_to_ankle|trunk_tilt_std|torso_lean_variability/.test(feature)) return "balance";
  if (/shoulder|hip_axis|separation|torso_lean|trunk_tilt/.test(feature)) return "rotation";
  if (/wrist|elbow|racket/.test(feature)) return "swing";
  if (/ankle|foot|step|launch|body_center|body_speed|hip_center|hip_speed|successor|braking/.test(feature)) return "movement";
  return footwork ? "movement" : "general";
}

function motionLabel(moment: ReportFocus["moment"], motion?: MotionAnalysis | null) {
  const labels = new Set(Object.values(motion?.families ?? {}).flatMap(family => family?.episodes ?? [])
    .filter(episode => moment && Number.isFinite(episode.start_ms) && Number.isFinite(episode.end_ms)
      && episode.start_ms <= moment.startMs && episode.end_ms >= moment.endMs)
    .map(episode => episode.classification?.status === "rule_inferred" ? episode.classification.label : "unclassified"));
  return labels.size === 1 ? [...labels][0] : "unclassified";
}

/** Short, low-intensity practice progressions, not inferred diagnoses or angle prescriptions. */
function practicePlan(group: PracticeGroup, moment?: ReportFocus["moment"], motion?: MotionAnalysis | null): PracticePlan {
  const label = motionLabel(moment, motion);
  const make = (title: string, goal: string, drill: string, retest: string, source: keyof typeof PRACTICE_SOURCES = "general"): PracticePlan => ({
    title, goal, drill, retest, nextStep: `${drill} 复测：${retest}`, source_links: [{ ...PRACTICE_SOURCES[source] }],
  });
  if (group === "head") return make("移动中保持看球节奏", "移动时继续跟踪来球，接球后自然回到准备位置。",
    "与同伴近距离抛接慢球：左右各移动一小步后接住球，再回到起点。做 2 组、每组 5 次，头部随来球自然转动，组间休息。",
    "保持相同抛球距离做 10 次，记录接住球并顺畅回位的次数；同机位录像比较头部移动，不以头部完全不动为目标。");
  if (group === "posture") return make("把准备姿势接到下一步", "用舒适的膝髋弯曲连接准备、移动和回位。",
    "在起点与左右两个近距离标记之间慢速往返。每次从自然准备姿势出发，到标记后回位；做 2 组、每组 5 次，组间休息，不刻意深蹲。",
    "保持同样标记与节奏做 10 次，记录连续完成准备、移动、回位的次数；同机位比较髋部高度与膝部变化。", "footwork");
  if (group === "movement") return make("练好启动后的调整与回位", "先朝来球方向移动，接近落点时用小步调整，随后回位。",
    "让同伴在左右近距离交替喂慢球，移动到球旁轻击，再回到准备标记。做 2 组、每组 5 球，组间休息，保持能从容完成的节奏。",
    "用同样喂球与标记复测 10 球，记录下一球发出前已回到准备位置的次数，先增加完成次数，再增加移动距离。", "footwork");
  if (group === "balance") return make("到位后顺畅接上下一球", "接近来球时调整脚步，保持能继续移动的准备姿势。",
    "从准备标记出发接同伴的近距离慢球，到球旁用小步调整后轻击，再回到标记。做 2 组、每组 5 球；站姿顺应来球，不要求双脚站死。",
    "用相同来球复测 10 球，记录能轻松击球并衔接回位的次数；录像对比到位后的双脚位置。", "footwork");
  if (label === "serve_motion" && (group === "swing" || group === "general" || group === "rotation")) return make("先把发球抛球练集中", "把抛球送到稳定、舒适的击球区域，再接上轻松挥拍。",
    "如果本段在练发球，先不挥拍，做 2 组、每组 5 次抛球，组间休息；保留自然站位，熟悉后再接轻松挥拍。",
    "同一站位再做 10 次，记录无需追球即可衔接挥拍的次数；稳定后再增加发球动作。", "serve");
  if (group === "rotation") return make("把转身与挥拍连起来", "让准备转身、轻松挥拍和回位形成连续节奏。",
    "先慢速空挥 5 次，再用熟悉的击球方式接近距离慢球，轻送到大目标区，做 2 组、每组 5 球。保持舒适转动幅度，组间休息。",
    "同样来球做 10 球，记录进入目标区的次数；同机位比较准备至回位是否更连贯，不追求更大的转体角度。");
  const stroke = label === "forehand" ? "正手" : label === "two_handed_backhand" ? "双手反手" : label === "backhand" ? "单手反手" : null;
  const opening = stroke ? `如果本段练的是${stroke}，使用熟悉的${stroke}握拍。` : "自选一种熟悉的击球方式。";
  const plan = make(group === "swing" ? "用小挥拍提高落点控制" : "先把慢球送进目标区", "用轻松、可重复的挥拍把球送到预先选定的目标。",
    `${opening}在小场地用低速球向大目标区轻打 2 组、每组 5 球，组间休息。先缩短距离和挥拍幅度，保持能连续对打的节奏。`,
    "用同样来球和目标复测 10 球，记录落入目标区的次数；连续完成后再稍微增加距离。");
  if (stroke) plan.source_links!.push({ ...(label === "forehand" ? PRACTICE_SOURCES.forehand : PRACTICE_SOURCES.backhand) });
  return plan;
}

function observationMoment(observation: IndicatorObservation, duration: number | null) {
  const window = observation.value === null ? observation.windows[0] : observation;
  return window && window.start_ms !== null && window.end_ms !== null && window.start_ms >= 0 && window.end_ms > window.start_ms && duration !== null && window.end_ms <= duration ? window : null;
}

function fallbackGuidance(focus: ReportFocus, duration: number | null, motion?: MotionAnalysis | null): TrainingGuidance {
  const capture = focus.label === "下次拍摄建议";
  const footwork = focus.moment?.kind === "footwork" || /步伐|脚/.test(focus.title);
  const moment = focus.moment && (duration === null || focus.moment.endMs <= duration) ? focus.moment : undefined;
  const plan = practicePlan(footwork ? "movement" : "general", moment, motion);
  return { ...focus, ...plan, moment, evidenceState: capture ? "unavailable" : "review", label: "基础练习",
    observation: capture ? "本次还没有这项动作的完整测量，可先建立一组自己的练习基线。" : moment ? `已定位${footwork ? "脚步移动" : "挥拍"}片段，可结合回放完成下面的练习。` : "本次已保留训练关注点，可先完成一组固定任务并记录结果。",
    detail: capture ? `下次固定机位，让${footwork ? "全身与双脚" : "肩髋与球拍"}持续入镜，并保留准备到恢复的完整过程。` : "练习目标按本次动作主题选择；下次使用相同任务与拍摄方式进行比较。",
    question: plan.goal, limitation: "这是基础技能练习；当前测量不足以给出这项动作的个体技术结论。",
  };
}

export function focusSelection(item: TrainingGuidance): TrainingFocusSelection {
  return { title: item.title, question: `${item.goal} 复测：${item.retest}`, ...(item.moment ? { timestampMs: item.moment.startMs } : {}) };
}

function conditionDifference(condition: CardCondition): string | null {
  if (condition.value === null || condition.passed !== false || condition.threshold === null) return null;
  if (Array.isArray(condition.threshold)) return condition.value < condition.threshold[0] ? "低于本项参考区间" : condition.value > condition.threshold[1] ? "高于本项参考区间" : null;
  return condition.operator === "gte" && condition.value < condition.threshold ? "低于本项参考值"
    : condition.operator === "lte" && condition.value > condition.threshold ? "高于本项参考值" : null;
}

/** Only recomputed, source-bound scored conditions can describe a measured shortfall. */
function scoredGuidance(result: DemoResultResponse, duration: number | null, motion?: MotionAnalysis | null) {
  const report = cardExecutionOf(result.card_execution);
  if (!report || !result.job_id || report.source_run_id !== result.job_id || !isFormalScoringPolicy(report.scoring_policy_version)) return [];
  return report.cards.flatMap(card => card.instances.flatMap(instance => {
    if (instance.status !== "scored" || instance.score === null || !instance.window) return [];
    const condition = instance.conditions.find(row => FEATURE_LABELS[row.metric] && conditionDifference(row));
    if (!condition) return [];
    const footwork = card.event_code.startsWith("FS"), group = practiceGroup(condition.metric, footwork);
    const { start_ms: startMs, end_ms: endMs } = instance.window;
    const moment: ReportFocus["moment"] = duration !== null && startMs >= 0 && endMs <= duration ? { id: instance.event_id, label: "本次计分片段", kind: footwork ? "footwork" : "rotation", startMs, endMs, note: "本次计分片段" } : undefined;
    const plan = practicePlan(group, moment, motion), label = guidanceLabel(condition.metric, "");
    const measured = `${label}为 ${Number(condition.value!.toFixed(2)).toLocaleString("zh-CN")} ${guidanceUnit(condition.unit)}`.trimEnd();
    const row: TrainingGuidance = { id: `scored:${card.indicator_id}:${instance.event_id}`, ...plan, moment,
      evidenceState: "observation", label: "本次评分重点", observation: `${measured}，${conditionDifference(condition)}。可以把${plan.goal.replace(/。$/, "")}作为本次练习目标。`,
      detail: `${measured}；当前计算参考值：${Array.isArray(condition.threshold) ? condition.threshold.join("–") : condition.threshold} ${guidanceUnit(condition.unit)}。本项得分 ${Number(instance.score.toFixed(1))}/100。`,
      question: plan.goal, limitation: "该结论针对本次测量片段与计算参考，不代表整场表现或动作问题的成因。" };
    return [{ row, group, score: instance.score }];
  })).sort((a, b) => a.score - b.score);
}

/** Observations choose a relevant skill; absent grades never become diagnoses. */
export function trainingGuidance(result: DemoResultResponse | null, motion?: MotionAnalysis | null): TrainingGuidance[] {
  if (!result) return [];
  const assessment = indicatorAssessmentOf(result.indicator_assessment);
  const duration = reportVideoDurationMs(result);
  const fallback = reportFocus(result, motion).map(item => fallbackGuidance(item, duration, motion));
  const seen = new Set<PracticeGroup>(), guidance: TrainingGuidance[] = [];
  for (const { row, group } of scoredGuidance(result, duration, motion)) {
    if (seen.has(group)) continue;
    seen.add(group); guidance.push(row);
    if (guidance.length === 2) return guidance;
  }
  for (const indicator of assessment?.indicators ?? []) {
    const observations = assessment!.observations.filter(item => indicator.observation_ids.includes(item.observation_id))
      .sort((a, b) => Number(!!observationMoment(b, duration)) - Number(!!observationMoment(a, duration)));
    const footwork = indicator.event_code.startsWith("FS");
    const observation = observations.find(row => !seen.has(practiceGroup(row.feature_name, footwork)));
    if (!observation) continue;
    const group = practiceGroup(observation.feature_name, footwork);
    seen.add(group);
    const label = guidanceLabel(observation.feature_name, observation.label_zh);
    const measured = observation.value === null ? `${label}取得 ${observation.windows.length} 段测量` : `${label}为 ${Number(observation.value.toFixed(2)).toLocaleString("zh-CN")} ${guidanceUnit(observation.unit)}`.trimEnd();
    const replay = observationMoment(observation, duration);
    const moment: ReportFocus["moment"] = replay ? { id: observation.observation_id, label, kind: footwork ? "footwork" : "rotation", startMs: replay.start_ms!, endMs: replay.end_ms!, note: "本次视频测量区间" } : undefined;
    const plan = practicePlan(group, moment, motion);
    const modelEstimate = observation.measurement_provenance?.observation_semantics === "monocular_model_estimate" || observation.measurement_provenance?.pose_source === "sam3d";
    guidance.push({ id: `observation:${indicator.indicator_id}`, ...plan, moment, label: "相关技能练习",
      detail: `本次${measured}。${observation.value === null ? "各局部片段分别保留，未合并为整段结论。" : "数值对应上方标注的测量区间。"}`,
      evidenceState: modelEstimate ? "review" : "observation", observation: `${modelEstimate ? "视频模型估计：" : "本段测量："}${measured}。先练下面这项技能，并记录自己的完成次数。`,
      question: plan.goal, limitation: "此测量用于选择练习主题，尚无适用判级时，不据此认定动作缺陷。单目估计受视角与遮挡影响。",
    });
    if (guidance.length === 2) break;
  }
  return guidance.length ? guidance : fallback;
}
