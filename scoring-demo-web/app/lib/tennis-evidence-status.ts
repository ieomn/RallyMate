/** Explain missing evidence without treating it as a low technique score. */
const reasons: Record<string, string> = {
  no_technique_specific_rules_applied: "具体技术类型尚未确认，仅采用通用挥拍项目。",
  required_evidence_not_observed: "未取得该项目所需的测量依据。",
  motion_phase_window_incomplete: "准备、挥出或随挥阶段不完整。",
  motion_time_discontinuous: "动作记录的时间不连续。",
  continuous_wrist_observations_missing: "缺少连续的手腕轨迹。",
  force_ground_contact_and_true_center_of_mass_unobserved: "未观测到真实受力、触地与身体重心，无法确认动力链。",
  racket_ball_contact_and_grip_unobserved: "缺少球拍、触球和握拍的独立证据。",
  independent_incoming_ball_pressure_or_reference_unobserved: "缺少来球时间压力或可比较的接发参考。",
  ball_release_contact_and_ground_contact_unobserved: "缺少抛球释放、触球或触地的独立证据。",
  body_relative_hand_or_hip_observations_missing: "缺少手腕或髋部的连续位置记录。",
  preparation_shoulder_axis_missing_or_foreshortened: "准备阶段肩部记录缺失，或机位无法分辨肩部转动。",
  acceleration_support_axes_missing_or_foreshortened: "加速阶段肩髋记录不足，或机位无法分辨其转动。",
  preparation_start_clipped_by_source_boundary: "准备动作的起点超出了可用片段。",
  preparation_rotation_or_arm_travel_below_measurement_floor: "准备时的转动或手臂位移太小，无法可靠测量。",
  motion_peak_order_unresolved: "无法可靠区分各部位运动峰值的先后。",
  motion_peak_two_sided_evidence_unresolved: "运动峰值前后的记录不足。",
  projected_motion_below_measurement_floor: "画面中的位移太小，无法可靠测量。",
  independent_forward_direction_unverified: "尚未独立确认向前的方向。",
  projected_forward_travel_below_measurement_floor: "画面中的前向位移太小，无法可靠测量。",
  simultaneous_two_wrist_motion_coverage_insufficient: "双腕同时可见的连续记录不足。",
  one_or_both_wrist_paths_below_measurement_floor: "一侧或两侧手腕的位移太小，无法可靠比较。",
  second_wrist_observations_missing: "缺少另一侧手腕的记录。",
  independent_forward_or_up_direction_unverified: "尚未独立确认向前和向上的方向。",
  slice_joint_directions_ill_conditioned: "当前机位无法可靠区分切削动作的方向。",
  independent_up_direction_unverified: "尚未独立确认向上的方向。",
  arm_points_missing_or_projected_segments_unobservable: "手臂关键点缺失，或机位无法分辨手臂转动。",
  arm_rotation_below_measurement_floor: "手臂转动太小，无法可靠测量。",
  arm_rotation_peak_order_unresolved: "无法可靠区分手臂各段转动的先后。",
  complete_recovery_boundary_unverified: "尚未确认完整回位动作的结束位置。",
};

export function tennisEvidenceReason(reason: string | null): string {
  return reason && Object.hasOwn(reasons, reason) ? reasons[reason] : "该项目所需的连续、可核验动作依据不足。";
}

/** Use bounded public messages, never raw server errors or internal paths. */
export function tennisAssessmentReadMessage(error: unknown): string {
  const status = error && typeof error === "object" && "status" in error ? error.status : null;
  if (status === 413) return "评分数据超过本次读取上限。分析任务已保留，请检查报告大小后重新读取。";
  if (status === 422) return "评分来源或测量数据未通过校验。请重新读取报告；本次未显示未经核验的分数。";
  if (status === 404) return "未找到当前任务的动作评分，请确认任务仍在分析服务中。";
  return "动作评分暂未读取成功，可能是连接中断或服务仍在计算。可重新读取，无需重新上传视频。";
}
