import type { DemoResultResponse, MotionAnalysis } from "./api-types";
import { indicatorAssessmentOf, type IndicatorObservation } from "./indicator-assessment";
import { reportFocus, type ReportFocus } from "./training-report";
import { reportVideoDurationMs } from "./workspace-navigation";

const FEATURE_LABELS: Record<string, string> = {
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
const guidanceLabel = (feature: string, label: string, indicator: string) => FEATURE_LABELS[feature] ?? (/[\u3400-\u9fff]/u.test(label) && !/[a-z]+_[a-z0-9_]+/iu.test(label) ? label : `${indicator}测量值`);
const guidanceUnit = (unit: string) => UNIT_LABELS[unit] ?? (/[\u3400-\u9fff]/u.test(unit) ? unit : unit ? "（单位见指标详情）" : "");

export type TrainingFocusSelection = { title: string; question: string; timestampMs?: number };
export type TrainingGuidance = ReportFocus & {
  evidenceState: "observation" | "review" | "unavailable";
  observation: string;
  question: string;
  nextStep: string;
  limitation: string;
  source_links?: Array<{ title: string; url: string }>;
};

// These links are deliberately static: imported report text cannot supply a URL.
const PRACTICE_SOURCES = {
  general: { title: "USTA 成人入门课表：示范、慢球与目标练习", url: "https://www.usta.com/content/dam/usta/content-fragments/tennis-service-center/assets/pdfs/USTA-Tennis-101-for-Adults.pdf" },
  forehand: { title: "USTA 正手入门示范与分级提示", url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--forehand.html" },
  backhand: { title: "USTA 反手入门示范与分级提示", url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/learning-the-basics--backhand.html" },
  serve: { title: "LTA 发球入门：抛球与逐步衔接挥拍", url: "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-serve-for-beginners/" },
} satisfies Record<string, { title: string; url: string }>;

/** A rule-inferred label only selects a conditional drill, never a confirmed diagnosis. */
function practiceStep(moment: ReportFocus["moment"], motion: MotionAnalysis | null | undefined, footwork: boolean) {
  const labels = new Set(Object.values(motion?.families ?? {}).flatMap(family => family?.episodes ?? [])
    .filter(episode => moment && Number.isFinite(episode.start_ms) && Number.isFinite(episode.end_ms)
      && episode.start_ms <= moment.startMs && episode.end_ms >= moment.endMs)
    .map(episode => episode.classification?.status === "rule_inferred" ? episode.classification.label : "unclassified"));
  const label = labels.size === 1 ? [...labels][0] : "unclassified";
  const prefix = "通用练习（并非已诊断的缺陷）：";
  const dosage = "以下次数只是起步安排，可按舒适程度减少。";
  const source_links = [{ ...PRACTICE_SOURCES.general }];
  if (label === "serve_motion") return {
    nextStep: `${prefix}若原片确认是在练发球，先看发球示范，只选“把球抛到同一片舒适击球区域”一个目标。先不挥拍，做 2 组、每组 5 次抛球，组间休息，记录需要追球的次数；熟悉后再衔接轻松挥拍。下次同一站位再做 10 次，只比较抛球是否更集中。${dosage}`,
    source_links: [{ ...PRACTICE_SOURCES.serve }],
  };
  const stroke = label === "forehand" ? "正手" : label === "two_handed_backhand" ? "双手反手" : label === "backhand" ? "单手反手" : null;
  if (stroke) source_links.push({ ...(label === "forehand" ? PRACTICE_SOURCES.forehand : PRACTICE_SOURCES.backhand) });
  const introduction = stroke ? `若原片确认是${stroke}，先看对应示范，保留自己熟悉的${stroke}握拍。` : "动作类型待确认，先看入门课表示范，自选一种熟悉的击球，不据此指定正手或反手。";
  const drill = footwork
    ? "在小场地用低速球与同伴合作，每次击球后回到预先选好的准备标记；这组只关注“回到标记等下一球”，不追求站死不动。做 2 组、每组 5 球，组间休息。下次用同样喂球与标记复测 10 球，记录能回到准备位置接下一球的次数。"
    : "在小场地用低速球向较大的目标区打 2 组、每组 5 球，组间休息；这组只关注“把球送进目标区”，不追求加速。下次用同样来球和目标复测 10 球，记录落入目标区的次数；稳定后再逐步增加距离或移动。";
  return { nextStep: `${prefix}${introduction}${drill}${dosage}`, source_links };
}

/** Questions describe what to check, never prescribe a correction or infer a defect. */
function reviewPrompt(feature: string, footwork: boolean) {
  if (/hip.*(?:height|_y_|vertical|reference_dy)|preload.*hip/.test(feature)) return {
    title: "看看身体高度怎样变化", question: "这段里，髋部高度在什么时刻开始变化？双脚当时在移动还是支撑？",
    nextStep: "先暂停在片段起点，再慢放到终点，记下高度变化与脚步发生的先后；下次保留同样的完整构图。",
  };
  if (/speed|acceleration|deceleration|slowdown/.test(feature)) return {
    title: footwork ? "回看移动与停稳的顺序" : "回看挥拍速度的变化区间", question: footwork ? "身体移动变慢前后，两只脚分别在什么位置？画面里能看清停稳的过程吗？" : "挥拍由快到慢的这段，肩、髋与手腕分别在怎样移动？",
    nextStep: footwork ? "先完整回放一次，再慢放检查双脚与身体停稳的顺序；下次录下动作结束后的恢复画面。" : "对照片段起止帧检查肩、髋与手腕；下次用同一机位保留完整准备与恢复，便于再次回看。",
  };
  if (/stance|ankle.*(?:width|distance)|support|hip_to_ankle/.test(feature)) return {
    title: "对照双脚与身体的位置", question: "片段前后，双脚间距和髋部相对双脚的位置怎样变化？是否有脚被遮挡？",
    nextStep: "暂停比较片段起点与终点，记录看得清的位置变化；下次让双脚和髋部持续留在画面内。",
  };
  if (/shoulder|hip_axis|separation|torso|trunk/.test(feature)) return {
    title: "对照肩髋的画面变化", question: "从片段起点到终点，肩线与髋线在画面中的方向怎样变化？两侧身体是否始终可见？",
    nextStep: "慢放这一段并各暂停一次起止帧，记录可看清的先后变化；下次保持相机固定，方便对照。",
  };
  if (/knee|elbow/.test(feature)) return {
    title: /knee/.test(feature) ? "回看膝部弯曲的变化" : "回看手臂展开的过程", question: /knee/.test(feature) ? "膝部在片段前后怎样弯曲和伸展？两条腿是否都能看清？" : "手臂在准备、挥拍与恢复时怎样变化？是否有身体遮挡？",
    nextStep: "先慢放对应区间，再记录一个能看清的变化；下次保持相同机位并保留动作前后的画面。",
  };
  return footwork ? {
    title: "先复盘这段脚步", question: "这段里，哪只脚先移动，身体什么时候跟上？开始和结束是否都能看清？",
    nextStep: "回看片段起止之间的双脚移动，只记下看得清的顺序；下次保留启动前与恢复后的画面。",
  } : {
    title: "先复盘这段挥拍", question: "这段准备、挥拍与恢复是否连贯可见？哪一个阶段需要再次暂停观察？",
    nextStep: "完整回放一次，再暂停到想复核的阶段；下次用同一机位记录同类动作，继续观察这个问题。",
  };
}

function observationMoment(observation: IndicatorObservation, duration: number | null) {
  const window = observation.value === null ? observation.windows[0] : observation;
  return window && window.start_ms !== null && window.end_ms !== null && window.start_ms >= 0 && window.end_ms > window.start_ms && duration !== null && window.end_ms <= duration ? window : null;
}

function fallbackGuidance(focus: ReportFocus, duration: number | null, motion?: MotionAnalysis | null): TrainingGuidance {
  const capture = focus.label === "下次拍摄建议";
  const footwork = focus.moment?.kind === "footwork" || /步伐|脚/.test(focus.title);
  const prompt = reviewPrompt("", footwork);
  // Historical reports may omit duration; existing validated episode bounds remain usable.
  const moment = focus.moment && (duration === null || focus.moment.endMs <= duration) ? focus.moment : undefined;
  const practice = practiceStep(moment, motion, footwork);
  return {
    ...focus, moment, evidenceState: capture ? "unavailable" : "review",
    observation: capture ? "当前缺少足够的连续画面或适用测量，暂时无法形成这一项动作判断。" : moment ? `已定位一段${footwork ? "步伐候选" : "挥拍运动"}区间，可回到原片核对。` : "报告保留了复核重点；当前没有可验证的片段定位。",
    question: capture ? footwork ? "下次是否能让双脚与身体在动作前后持续可见？" : "下次是否能让两侧肩、髋在固定机位中持续可见？" : prompt.question,
    nextStep: `${practice.nextStep}${capture ? ` 复拍时：${focus.detail}` : ""}`,
    source_links: practice.source_links,
    limitation: capture ? "缺少证据不代表动作没有发生，也不代表技术较差。" : "动作类型与技术好坏仍需复核；运动候选不等于已确认触球。",
  };
}

export function focusSelection(item: TrainingGuidance): TrainingFocusSelection {
  return { title: item.title, question: item.question, ...(item.moment ? { timestampMs: item.moment.startMs } : {}) };
}

/** Suggestions explain observed motion, never infer a technical error from coverage. */
export function trainingGuidance(result: DemoResultResponse | null, motion?: MotionAnalysis | null): TrainingGuidance[] {
  const assessment = indicatorAssessmentOf(result?.indicator_assessment);
  const duration = reportVideoDurationMs(result);
  const fallback = reportFocus(result, motion).map(item => fallbackGuidance(item, duration, motion));
  if (!assessment?.observations.length) return fallback;
  const seen = new Set<string>();
  const guidance: TrainingGuidance[] = [];
  for (const indicator of assessment.indicators) {
    const observation = assessment.observations.filter(item => indicator.observation_ids.includes(item.observation_id))
      .sort((a, b) => Number(!!observationMoment(b, duration)) - Number(!!observationMoment(a, duration)))[0];
    if (!observation || seen.has(indicator.event_code)) continue;
    seen.add(indicator.event_code);
    const footwork = indicator.event_code.startsWith("FS");
    const motionName = footwork ? "脚步" : "挥拍";
    const label = guidanceLabel(observation.feature_name, observation.label_zh, indicator.name_zh);
    const measured = observation.value === null ? `${label}取得 ${observation.windows.length} 段局部测量，尚不足以汇总整个阶段` : `${label}为 ${Number(observation.value.toFixed(2)).toLocaleString("zh-CN")} ${guidanceUnit(observation.unit)}`.trimEnd();
    const prompt = reviewPrompt(observation.feature_name, footwork);
    const replay = observationMoment(observation, duration);
    const moment: ReportFocus["moment"] = replay ? { id: observation.observation_id, label: indicator.name_zh, kind: footwork ? "footwork" : "rotation", startMs: replay.start_ms!, endMs: replay.end_ms!, note: observation.definition_zh } : undefined;
    const practice = practiceStep(moment, motion, footwork);
    const modelEstimate = observation.measurement_provenance?.observation_semantics === "monocular_model_estimate" || observation.measurement_provenance?.pose_source === "sam3d";
    guidance.push({
      id: `observation:${indicator.indicator_id}`, title: prompt.title, label: modelEstimate ? "模型估计 · 待原片复核" : "已取得局部测量",
      // Keep the precise value available to exports and the collapsed evidence detail.
      detail: `本次${measured}。对应${motionName}指标：${indicator.name_zh}。${observation.definition_zh}`,
      evidenceState: modelEstimate ? "review" : "observation",
      observation: modelEstimate ? `本段提供了${label}的模型估计，可对照原片查看。` : observation.value === null ? `本段取得了${label}的 ${observation.windows.length} 段局部测量，尚未覆盖整个阶段。` : `本段已取得${label}的画面测量，可回到对应区间核对。`,
      question: prompt.question, nextStep: practice.nextStep, source_links: practice.source_links,
      limitation: modelEstimate ? "模型估计不等于可见性已验证；不能据此判断动作好坏或真实发力。" : "画面测量不能单独判定技术好坏；动作类型、完整阶段与触球仍需复核。",
      moment,
    });
    if (guidance.length === 2) break;
  }
  return guidance.length ? [...guidance, ...fallback.filter(item => item.evidenceState === "unavailable")].slice(0, 3) : fallback;
}
