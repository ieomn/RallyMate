"""Rebuild the reviewed card contract from the immutable audited Word snapshot."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "card-rules-v1.0.0"
TORSO = ["left_shoulder", "right_shoulder", "left_hip", "right_hip"]
LOWER = TORSO + ["left_knee", "right_knee", "left_ankle", "right_ankle"]
ARMS = TORSO + ["left_elbow", "right_elbow", "left_wrist", "right_wrist"]
SUPPORT = ["left_hip", "right_hip", "left_ankle", "right_ankle"]
WRISTS = TORSO + ["left_wrist", "right_wrist"]
FEET = TORSO + ["left_ankle", "right_ankle"]
HEAD = ["nose", "left_ear", "right_ear"]
GRADE_MAPPING = {"A": 100, "B": 80, "C": 60, "D": 40, "E": 20}
METRICS = {}


def metric(key, unit, joints, definition, *, camera=False, dependencies=()):
    METRICS[key] = {"unit": unit, "required_joint_names": joints,
        "definition": definition, "camera_reference_required": camera,
        "dependencies": list(dependencies)}


for side in ("left", "right"):
    joints = [f"{side}_{name}" for name in ("hip", "knee", "ankle")]
    metric(f"{side}_knee_flexion_deg.delta", "deg", joints,
           "末端连续有效帧中位数减起始连续有效帧中位数；每帧值为180减二维髋膝踝夹角。两端各20%窗口，至少3帧。")
    metric(f"{side}_knee_flexion_deg.range", "deg", joints,
           "完整绑定阶段的二维膝屈曲角最大值减最小值。")
for axis, joints in (("shoulder", TORSO[:2]), ("hip", TORSO[2:])):
    metric(f"{axis}_axis_deg.range", "deg", joints,
           "连续展开180度周期的画面轴线角度最大值减最小值；只代表投影变化。")
metric("shoulder_hip_separation_deg.delta", "deg", TORSO,
       "末端减起始20%连续帧中位数；每帧肩线与髋线夹角折叠至0至90度，非三维扭转。")
metric("shoulder_hip_separation_deg.range", "deg", TORSO,
       "完整阶段肩髋二维最小夹角的最大值减最小值。")
metric("shoulder_hip_separation_deg.mean", "deg", TORSO,
       "完整绑定阶段共同有效帧的肩髋二维最小夹角均值；只反映投影分离，不代表三维扭转。")
metric("trunk_tilt_deg.range", "deg", TORSO,
       "肩中点至髋中点向量相对画面竖直的绝对角度范围。")
metric("trunk_tilt_deg.median", "deg", TORSO,
       "肩中点至髋中点向量相对画面竖直的绝对角度中位数。")
metric("hip_inside_ankle_support_fraction.mean", "fraction", SUPPORT,
       "有效帧中髋中心x位于两踝x闭区间的比例；不代表足底真实接触或受力。")
metric("hip_to_ankle_midpoint_torso.range", "torso", FEET,
       "(髋中心x减双踝中点x)/同帧躯干长度的范围；投影平衡代理。")
metric("wrist_separation_torso.median", "torso", WRISTS,
       "两腕距离/肩中心至髋中心距离的中位数；不能证明握住球拍。")
metric("wrist_separation_torso.delta", "torso", WRISTS,
       "末端减起始20%连续帧双腕距离/躯干长度中位数；不等同手拍接触。")
metric("ankle_distance_over_hip_width.median", "ratio", FEET,
       "双踝画面横向距离/双髋二维距离的中位数；髋宽小于躯干长度0.18时不可计算。")
metric("hip_displacement_torso", "torso", TORSO,
       "固定相机参考下髋中心首尾20%帧中位坐标距离/本窗固定躯干长度中位数。", camera=True)
metric("hip_lateral_displacement_torso", "torso", TORSO,
       "固定相机参考下髋中心首尾20%帧中位x之差绝对值/固定躯干尺度；不等同球场横向。", camera=True)
metric("hip_downward_displacement_torso", "torso", TORSO,
       "固定相机参考下末端减起始20%帧中位髋y/固定躯干尺度；画面y向下为正。", camera=True)
metric("hip_speed_std_torso_s", "torso/s", TORSO,
       "相邻连续帧髋中心欧氏位移除真实时间间隔和固定躯干尺度，再取总体标准差。", camera=True)
metric("hip_speed_drop_torso_s", "torso/s", TORSO,
       "完整绑定阶段最前20%连续速度中位数减最后20%连续速度中位数。", camera=True)
metric("wrist_speed_increase_torso_s", "torso/s", WRISTS,
       "持拍侧腕在固定相机参考的末端20%速度中位数减起始20%速度中位数；需要明确持拍侧。", camera=True, dependencies=["handedness"])
metric("ankle_order_exchange_count", "count", FEET,
       "在完整连续步周期中左踝x减右踝x越过±0.08固定躯干长度死区的有效换序次数；只有端点交叉不计。", camera=True, dependencies=["complete_step_cycle"])
metric("ankle_max_displacement_torso", "torso", FEET,
       "左右踝在完整连续步周期的首尾20%坐标中位数位移/固定躯干长度的较大者。", camera=True, dependencies=["complete_step_cycle"])
metric("ankle_width_contraction_torso", "torso", FEET,
       "完整并步周期两踝距离的中段最大值减末端20%中位数，除固定躯干长度；要求先迈出再跟随。", camera=True, dependencies=["complete_step_cycle"])
metric("ankle_stability_ratio", "ratio", FEET,
       "完整跨步周期两踝轨迹长度中较小值/较大值；分母低于0.1固定躯干长度拒绝计算。", camera=True, dependencies=["complete_step_cycle"])
metric("head_relative_motion_range_torso", "torso", ["nose"] + TORSO,
       "鼻点减肩中心并按躯干尺度归一化后的最大两帧欧氏距离；只能评价头相对躯干稳定。")



# Explicit M10 revisions: body stability, projected return, and racket control
# are distinct behaviors. These definitions do not change the source wording.
for key, description in (
    ("shoulder_reference_error_reduction_deg", "肩轴至独立动作前准备参照的绝对180度周期误差：起端20%中位数减末端20%中位数，各端至少3帧。"),
    ("hip_reference_error_reduction_deg", "髋轴至独立动作前准备参照的绝对180度周期误差：起端20%中位数减末端20%中位数，各端至少3帧。"),
    ("terminal_max_reference_error_deg", "末端20%至少3帧内，肩/髋轴至各自独立准备参照的绝对周期误差最大值。"),
    ("terminal_max_axis_range_deg", "末端20%至少3帧内肩/髋连续展开轴角范围的较大值，非三维朝向。"),
):
    metric(key, "deg", TORSO, description, camera=True, dependencies=["independent_pre_action_body_ready_reference"])
metric("terminal_hip_speed_torso_s", "torso/s", FEET,
    "末端20%至少3帧内部相邻髋中心位移/实际PTS间隔/完整M10固定躯干中位尺度的速度中位数。", camera=True)
metric("terminal_hip_inside_ankle_support_fraction", "fraction", FEET,
    "末端20%至少3帧中髋中心x处于两踝x闭区间的比例；不证明接地或承重。", camera=True)
metric("terminal_trunk_tilt_range_deg", "deg", FEET,
    "末端20%至少3帧中躯干相对图像竖直倾角的最大值减最小值。", camera=True)


def c(key, operator, value, description):
    spec = METRICS[key]
    row = {"key": key, "metric": key, "operator": operator, "unit": spec["unit"],
           "required": True, "description": description,
           "measurement_semantics": spec["definition"],
           "required_joint_names": spec["required_joint_names"],
           "threshold_basis": "engineering_noise_or_proxy_check_v1_not_source_or_coach_calibration"}
    row["bounds" if operator == "between" else "value"] = value
    return row


BALANCE = [c("hip_inside_ankle_support_fraction.mean", "gte", .8, "髋投影大部分时间处于双踝区间"),
           c("trunk_tilt_deg.range", "lte", 15, "躯干投影倾角没有大幅摆动")]
STABILITY = [c("hip_speed_std_torso_s", "lte", .3, "固定参考的髋速度波动有限"),
             c("trunk_tilt_deg.range", "lte", 15, "躯干投影姿态稳定")]
LOWERING = [c("hip_downward_displacement_torso", "gte", .03, "固定相机参考内髋中心下移"),
            c("left_knee_flexion_deg.delta", "gte", 4, "左膝屈曲增加"),
            c("right_knee_flexion_deg.delta", "gte", 4, "右膝屈曲增加")]
TURN = [c("shoulder_axis_deg.range", "gte", 8, "肩线发生可分辨二维旋转"),
        c("hip_axis_deg.range", "gte", 4, "髋线发生可分辨二维旋转")]
MOVE = [c("hip_displacement_torso", "gte", .15, "身体中心在稳定相机参考内移动"),
        c("trunk_tilt_deg.range", "lte", 20, "移动中保持有限躯干倾角变化")]
HAND_NEAR = [c("wrist_separation_torso.median", "lte", .65, "双腕保持接近；不确认球拍握持或三角区")]
HAND_OPEN = [c("wrist_separation_torso.delta", "gte", .15, "双腕分离增大；不确认非持拍手具体接触状态")]
READY = BALANCE + [c("wrist_separation_torso.median", "lte", .65, "双腕接近的距离代理；是否位于身前须另证")]


def evaluation(family, conditions, *, kind="visual_proxy", limitation="只评价所列二维运动条件，不等于完整原文行为或技术质量。"):
    unique = {}
    for condition in conditions:
        if condition["key"] in unique and any(unique[condition["key"]].get(k) != condition.get(k) for k in ("metric", "operator", "value", "bounds", "unit")):
            raise ValueError("conflicting duplicated condition: " + condition["key"])
        unique[condition["key"]] = condition
    return {"kind": kind, "family": family, "conditions": deepcopy(list(unique.values())),
        "grade_policy": "condition_completion_v1", "grade_mapping": GRADE_MAPPING,
        "source_grade_supported": False, "numeric_basis": "engineering_v1_uncalibrated",
        "source_behavior_coverage": "partial_observations_only" if kind == "visual_proxy" else "requires_additional_evidence",
        "output_semantics": "engineering_proxy_condition_completion" if kind == "visual_proxy" else "manual_evidence_required",
        "limitation": limitation, "extractor_status": "registry_and_runtime_evidence_required"}



def m10_evaluation(identifier):
    rotation = {"GS01-M10-02", "GS02-M10-02", "GS03-M10-03"}
    stability = {"GS01-M10-03", "GS02-M10-03", "GS03-M10-04"}
    missing = {
        "GS01-M10-01": ("bilateral_hand_racket_control_source_required", ["source_bound_bilateral_hand_identity", "source_bound_racket_handle_or_contact_region", "bilateral_hand_racket_control_sequence"]),
        "GS02-M10-01": ("continuous_two_hand_racket_control_source_required", ["source_bound_bilateral_hand_identity", "source_bound_racket_handle_geometry", "continuous_two_hand_racket_control_sequence"]),
        "GS03-M10-01": ("non_racket_hand_to_throat_contact_source_required", ["source_bound_non_racket_hand_identity", "source_bound_racket_throat_RK013", "non_racket_hand_throat_recontact_sequence"]),
        "GS03-M10-02": ("racket_hand_handle_control_source_required", ["source_bound_racket_hand_identity", "source_bound_racket_handle_and_axis", "racket_hand_handle_control_and_body_front_sequence"]),
        "GS01-M10-04": ("complete_next_shot_ready_state_source_required", ["body_recovery_geometry", "bilateral_hand_racket_control_sequence", "source_bound_successor_split_or_first_step"]),
        "GS02-M10-04": ("complete_two_hand_ready_posture_source_required", ["body_recovery_geometry", "continuous_two_hand_ready_grip_source", "independent_two_hand_ready_posture_reference"]),
        "GS03-M10-05": ("complete_generic_ready_state_source_required", ["body_recovery_geometry", "bilateral_hand_racket_control_sequence", "source_bound_racket_in_front_of_body"]),
    }
    if identifier in rotation:
        result = evaluation("projected_axis_return_to_independent_reference", [
            c("shoulder_reference_error_reduction_deg", "gte", 8, "肩轴接近独立准备方向参照"),
            c("hip_reference_error_reduction_deg", "gte", 8, "髋轴接近独立准备方向参照"),
            c("terminal_max_reference_error_deg", "lte", 15, "末段肩髋轴保持在准备参照的工程容差内"),
            c("terminal_max_axis_range_deg", "lte", 8, "末段肩髋投影轴不大幅摆动")],
            limitation="只评价相对独立动作前准备参照的二维轴投影返回，不证明三维胸廓/骨盆朝向。无同源连续参照则缺测，不用末端自参考或身体稳定代替。8度/15度为预先固定工程分辨与容差，非原文阈值或教练校准。")
        result.update(score_block_reason_code="independent_ready_reference_missing", required_observables=["independent_pre_action_body_ready_reference", "continuous_paired_shoulder_hip_axis_geometry"])
    elif identifier in stability:
        result = evaluation("terminal_body_projection_stability", [
            c("terminal_hip_speed_torso_s", "lte", .6, "末段髋中心图像运动速度较低"),
            c("terminal_hip_inside_ankle_support_fraction", "gte", .8, "末段髋投影大部分位于双踝区间"),
            c("terminal_trunk_tilt_range_deg", "lte", 15, "末段躯干投影摆动有限")],
            limitation="完整恢复窗中的末段身体投影稳定条件；不将匀速快移的低速度标准差当作稳定，不证明足底接地、受力或完整准备状态。")
        result.update(score_block_reason_code="continuous_recovery_body_geometry_required", required_observables=["continuous_source_bound_body_recovery_geometry"])
    elif identifier in missing:
        reason, requirements = missing[identifier]
        result = evaluation("m10_racket_or_complete_ready_observables_required", [], kind="manual_review",
            limitation="此卡需要明确的手—球拍关系或完整准备状态来源；双腕相对位置、身体稳定或末段静止不能代替。可测人体几何保留为辅助，缺必要来源时不评分。")
        result.update(score_block_reason_code=reason, required_observables=requirements,
            auxiliary_observations=[{"metric": key, "unit": METRICS[key]["unit"], "required_joint_names": METRICS[key]["required_joint_names"],
                "measurement_semantics": METRICS[key]["definition"], "scoring_eligible": False}
                for key in ("wrist_separation_torso.median", "wrist_separation_torso.delta", "shoulder_axis_deg.range", "hip_axis_deg.range", "trunk_tilt_deg.range", "hip_inside_ankle_support_fraction.mean")])
    else:
        return None
    result.update(scoring_revision_id="gs-m10-observability-v1.0.0", required_engine="gs-recovery-evidence-v1.0.0")
    return result


def gs_evaluation(rule):
    """Reviewed behavior families; output retains card-specific source definition."""
    identifier = rule["indicator_id"]
    name = rule["name_zh"]
    recovery = m10_evaluation(identifier)
    if recovery is not None:
        return recovery
    if identifier in {"GS01-M09-04", "GS01-M09-05"}:
        approach = identifier.endswith("04")
        result = evaluation("non_racket_hand_approach_requires_racket_source" if approach else "racket_control_reentry_requires_sequence", [], kind="manual_review",
            limitation=("非持拍手接近球拍需要明确手侧、同源球拍几何及手到球拍的连续相对运动；双腕靠近不能代替。缺少必要球拍来源时不评分。"
                if approach else "球拍重新进入非持拍手控制需要同源手与球拍接触区域及重新接触或控制时序；双腕靠近或末端稳定不能代替。接触后支持窗必须另行绑定，不暗扩M09。缺少必要来源时不评分。"))
        result.update(scoring_revision_id="gs01-m09-hand-racket-observability-v1.0.0",
            score_block_reason_code="non_racket_hand_to_racket_source_required" if approach else "hand_racket_control_sequence_required",
            required_observables=["source_bound_non_racket_hand_identity", "source_bound_racket_geometry", "continuous_hand_to_racket_approach"]
                if approach else ["source_bound_non_racket_hand_identity", "source_bound_racket_handle_or_contact_region", "hand_racket_recontact_or_control_sequence"],
            auxiliary_observations=[{"metric": "wrist_separation_torso.delta", "unit": "torso", "required_joint_names": WRISTS,
                "measurement_semantics": METRICS["wrist_separation_torso.delta"]["definition"],
                "description": "双腕距离变化仅为人体相对几何附属观察，不证明手向球拍接近、接触或控制。", "scoring_eligible": False}])
        return result
    if identifier == "GS01-M03-04":
        result = evaluation("racket_side_loading_review", [], kind="manual_review",
            limitation="任意方向髋位移不能证明向持拍侧加载。需绑定持拍侧、支撑脚与髋相对支撑变化，再区分视觉侧移和真实承重；现有无方向位移只作附属观察，不判加载代理等级。")
        result["auxiliary_observations"] = deepcopy(MOVE)
        return result
    if identifier == "GS01-M06-03":
        return evaluation("pre_acceleration_posture_review", [], kind="manual_review",
            limitation="此卡评价加速前的持拍手姿态；实际腕加速是本阶段结束/M07开始标志，不能用腕速度增加替代准备姿态。需结合持拍侧肘腕相对位置及球拍状态建立专项规则。")
    if identifier in {"GS01-M04-01", "GS02-M04-01", "GS03-M04-01"}:
        result = evaluation("support_leg_push_review", [], kind="manual_review",
            limitation="不能要求双膝同时伸展来评价支撑腿蹬地。先确认本拍支撑侧，再检查该侧膝伸展与髋运动的连续时序；原文主动受力仍需专项或人工证据。双膝角度可以展示但不据此自动给蹬地等级。")
        result["auxiliary_observations"] = [c("left_knee_flexion_deg.delta", "lte", -4, "左膝伸展局部观察，不预设支撑侧"), c("right_knee_flexion_deg.delta", "lte", -4, "右膝伸展局部观察，不预设支撑侧")]
        return result
    if identifier in {"GS02-M08-01", "GS03-M08-01"}:
        result = evaluation("contact_control_review", [], kind="manual_review",
            limitation="此卡主体是双手/单手稳定控制球拍击球。髋投影支撑与躯干稳定不能证明触球或手拍控制；必须先有活动球/球拍接触和手部控制证据。身体平衡可作为局部观察但不据此给击球等级。")
        result["auxiliary_observations"] = deepcopy(BALANCE)
        return result
    if identifier in {"GS01-M04-06", "GS01-M05-06", "GS03-M05-05"}:
        result = evaluation("rotation_preparation_review", [], kind="manual_review",
            limitation="准备骨盆/胸廓启动不等于已经完成主动旋转；需准备姿态、支撑状态与后继旋转起点的专项相对时序。当前轴线变化只作附属测量，不能反向要求准备阶段先旋转。")
        result["auxiliary_observations"] = deepcopy(TURN)
        return result
    if identifier in {"GS04-M04-02", "GS04-M05-04", "GS05-M04-02", "GS05-M05-04"}:
        result = evaluation("sideways_posture_maintenance_review", [], kind="manual_review",
            limitation="保持侧身不能以持续转肩/转髋角度增加判定。需场地方向、身体朝向和参考侧身姿态；静止侧身可正确完成此项。二维轴线范围可展示但不作为该项等级。")
        result["auxiliary_observations"] = deepcopy(TURN)
        return result
    if identifier == "GS01-M05-05":
        return evaluation("projected_separation_maintenance", [
            c("shoulder_hip_separation_deg.mean", "gte", 5, "存在可分辨的肩髋投影分离"),
            c("shoulder_hip_separation_deg.range", "lte", 15, "肩髋投影分离在该段维持有限波动")],
            limitation="仅评价肩髋二维投影分离存在与保持，不确认真实三维分离角、非持拍手主动配平或动力链。均值5度是工程噪声下限，范围15度是未标定稳定性检查，不是原文优劣阈值。")
    if identifier in {"GS03-M07-03", "GS03-M09-02", "GS04-M07-04", "GS04-M09-04", "GS05-M07-04", "GS05-M09-04"}:
        return evaluation("arm_spread_posture_proxy", [c("wrist_separation_torso.median", "gte", .8, "双腕保持可分辨的展开距离；不要求该阶段继续分离")] + BALANCE,
            limitation="只评价双腕二维展开距离与身体支撑投影，不确认非持拍手方向、球拍接触或上肢受力。0.8躯干长度为未标定工程几何下限；已张开的姿态无需继续增加腕距。")
    if any(word in name for word in ("盯球", "观察")):
        return evaluation("visual_attention", [], kind="manual_review", limitation="头部朝向不能直接确认眼球真实注视。可附头部稳定测量，原文观察行为需人工逐段复核。")
    if any(word in name for word in ("反作用力", "动力向", "能量", "背部持续收紧", "下旋")):
        return evaluation("unobserved_physical_quantity", [], kind="manual_review", limitation="单目人体坐标不能测得地面反作用力、能量传递、肌肉收缩或球自转；需传感器、专项跟踪或人工证据。")
    if any(word in name for word in ("拍面", "拍头", "球拍轨迹", "球拍到达", "球拍与球接触", "球离开拍面", "摩擦来球", "切削触球", "球拍接近", "预计击球点")):
        return evaluation("racket_ball_detail", [], kind="manual_review", limitation="需球拍关键点和活动球轨迹及真实触球证据；人体腕点或估计加速峰不替代。")
    if "降低重心" in name:
        result = evaluation("local_preload_window_review", [], kind="manual_review",
            limitation="降低重心可能发生在准备阶段中部，垫步落地后又回到原高。整阶段首尾差不能代替局部下压；需关联同人同视频且位于本GS阶段内的预加载连续子窗，联合核实髋下降与双膝屈曲。")
        result["required_engine"] = "temporal_preload_subwindow_required"
        result["auxiliary_observations"] = deepcopy(LOWERING)
        result["fallback_binding"] = {"source_card": "FS01-M02", "same_person_video_required": True,
            "contained_in_gs_stage_required": True, "requires_confirmed_split_step": True,
            "reuse_score": False, "recompute_on_bound_local_window": True,
            "local_dip_only": "needs_review_no_grade"}
        return result
    if "肩髋分离逐渐释放" in name:
        return evaluation("projected_separation_release", [c("shoulder_hip_separation_deg.delta", "lte", -5, "肩髋投影分离缩小"), c("shoulder_axis_deg.range", "gte", 8, "肩线继续投影旋转")])
    if "肩髋分离" in name:
        return evaluation("projected_separation", [c("shoulder_hip_separation_deg.range", "gte", 5, "肩髋投影分离出现变化")])
    if "骨盆" in name or "转髋" in name:
        return evaluation("projected_pelvis_rotation", [c("hip_axis_deg.range", "gte", 8, "髋线投影发生旋转")])
    if "胸廓" in name or "胸腔" in name:
        return evaluation("projected_thorax_rotation", [c("shoulder_axis_deg.range", "gte", 8, "肩线投影发生旋转")])
    if "加速" in name:
        return evaluation("hitting_wrist_acceleration", [c("wrist_speed_increase_torso_s", "gte", .4, "持拍侧腕速度在该阶段提高")], limitation="只评价持拍腕二维速度变化，不能确认球拍释放、双手发力配比或三维加速。")
    if any(word in name for word in ("自然张开", "主动释放", "自然离开", "主动配平")):
        return evaluation("arm_separation", HAND_OPEN + BALANCE)
    if any(word in name for word in ("重新接触", "主动接近", "重新进入")):
        return evaluation("hands_reapproach", [c("wrist_separation_torso.delta", "lte", -.15, "双腕靠近")], limitation="双腕靠近只是接拍候选，手拍接触须球拍证据复核。")
    if any(word in name for word in ("旋转恢复", "恢复稳定")):
        return evaluation("recovery_stability", STABILITY + BALANCE)
    if "准备姿态" in name or "准备状态" in name or "通用准备" in name or "准备启动" in name:
        return evaluation("ready_posture", READY, limitation="姿态投影代理；完整准备状态还须球拍控制与下一动作时序。")
    if any(word in name for word in ("单位转体", "转肩", "身体转动", "身体继续转", "身体继续旋转", "身体继续向场内旋转", "侧身")):
        return evaluation("projected_unit_turn", TURN, limitation="肩髋二维投影改变，正背机位轴线缩短时不能恢复真实侧身角；转肩与持拍复合项只评价身体部分。")
    if any(word in name for word in ("重心向", "重心持续", "身体移动", "重心加载", "重心持续移动")):
        return evaluation("projected_body_translation", MOVE, limitation="只确认相机参考内二维移动；向前/后、承重侧须球场标定/持拍侧证据，不能仅凭画面x或y判断。")
    if "主动蹬地" in name:
        return evaluation("leg_extension_proxy", [c("left_knee_flexion_deg.delta", "lte", -4, "左膝伸展"), c("right_knee_flexion_deg.delta", "lte", -4, "右膝伸展")], limitation="双膝投影伸展不等于实际蹬地或足底受力，单腿技术须人工判定支撑侧后重算。")
    if "垫步" in name or "第一步" in name or "调整步伐" in name:
        return evaluation("footwork_event_link", [], kind="manual_review", limitation="须关联对应完整FS事件与连续步周期；不能用单帧踝距或低速宣称完成垫步/第一步。")
    if any(word in name for word in ("稳定", "平衡", "支撑")):
        return evaluation("projected_dynamic_balance", BALANCE)
    if any(word in name for word in ("双手", "非持拍手", "握拍", "球拍三角区", "拍柄")):
        return evaluation("hand_relation_proxy", HAND_NEAR, limitation="双腕距离只表示手的相对位置；不证明握拍、辅助手位于拍喉或双手控制球拍。")
    return evaluation("racket_motion_detail", [], kind="manual_review", limitation="该项主体为球拍动作或触球后的专项轨迹；只有人体姿态时须复核，不能将腕轨迹直接当拍头轨迹。")


def fs_evaluation(rule):
    code = rule["indicator_id"]
    if code in {"FS05-M03", "FS05-M04"}:
        result = evaluation("target_relative_micro_adjustment_review", [], kind="manual_review",
            limitation="微调需要目标击球点、场地方向及小步幅上界；任意较大二维位移不能替代前后/左右微调。当前只保留位移测量，缺少目标与步周期时不评分。")
        result["auxiliary_observations"] = deepcopy(MOVE)
        return result
    if code in {"FS06-M05", "FS07-M05"}:
        return evaluation("static_support_readiness_proxy", BALANCE,
            limitation="只评价当前支撑投影与躯干稳定。已经稳定的准备姿态不要求再次减速；开放/闭合分类、足底支撑及后继击球衔接需另证，不由此身体局部代理确认。")
    if code in {"FS01-M02", "FS01-M05", "FS09-M03", "FS09-M04", "FS09-M05"}:
        joint_specs = {
            "hip_drop_torso": ("torso", LOWER, "连续联合下降子窗内固定相机参考的髋中心下移/固定躯干尺度。"),
            "left_knee_flexion_change_deg": ("deg", LOWER, "同一连续子窗末端减起始左膝二维屈曲角。"),
            "right_knee_flexion_change_deg": ("deg", LOWER, "同一连续子窗末端减起始右膝二维屈曲角。"),
            "joint_descent_fraction": ("fraction", LOWER, "同一连续预加载子窗内双膝屈曲与髋下降联合成立的区间比例。"),
            "joint_braking_fraction": ("fraction", LOWER, "同一连续制动吸收子窗内速度下降、髋下降、双膝屈曲同时成立的相邻区间时间比例。"),
            "speed_step_variation_ratio": ("ratio", LOWER, "同一连续预加载子窗内身体速度相邻变化量与典型速度的比值。"),
            "hip_inside_support_fraction": ("fraction", LOWER, "落地或制动后的连续子窗内髋投影位于双踝区间的有效帧比例。"),
            "body_speed_std_torso_s": ("torso/s", LOWER, "落地或制动后的连续子窗内固定参考身体速度的标准差。"),
            "successor_interval_ms": ("ms", LOWER, "本卡已绑定的落地/稳定子窗结束至同人同视频下一所需FS事件开始的时差。"),
            "hip_speed_drop_torso_s": ("torso/s", LOWER, "制动后的同一连续吸收子窗起始减末端髋中心速度。"),
            "hip_speed_drop_fraction": ("fraction", LOWER, "制动后的同一连续吸收子窗髋速度下降量/起始速度；起始近零拒绝计算。"),
            "trunk_tilt_std_deg": ("deg", LOWER, "连续制动吸收子窗内躯干二维投影倾角标准差。"),
            "supported_low_motion_duration_ms": ("ms", LOWER, "连续子窗内髋速度≤0.6躯干/s、双踝各≤0.5躯干/s并且髋投影在双踝区间的持续时间；不是足底接触证据。"),
            "shoulder_hip_angular_speed_drop_deg_s": ("deg/s", LOWER, "制动前后连续子窗肩髋投影轴角速度下降量。"),
        }
        for key, (unit, joints, definition) in joint_specs.items():
            if key not in METRICS:
                metric(key, unit, joints, definition, camera=True, dependencies=["temporal_card_evidence"])
        checks = {
            "FS01-M02": [("hip_drop_torso", "gte", .03), ("left_knee_flexion_change_deg", "gte", 4), ("right_knee_flexion_change_deg", "gte", 4), ("joint_descent_fraction", "gte", .7), ("speed_step_variation_ratio", "lte", 1.5)],
            "FS01-M05": [("hip_inside_support_fraction", "gte", .8), ("body_speed_std_torso_s", "lte", .3), ("successor_interval_ms", "between", [0, 500])],
            "FS09-M03": [("hip_speed_drop_torso_s", "gte", .2), ("hip_speed_drop_fraction", "gte", .25), ("left_knee_flexion_change_deg", "gte", 4), ("right_knee_flexion_change_deg", "gte", 4), ("hip_drop_torso", "gte", .03), ("trunk_tilt_std_deg", "lte", 10), ("joint_braking_fraction", "gte", .5)],
            "FS09-M04": [("hip_inside_support_fraction", "gte", .8), ("supported_low_motion_duration_ms", "gte", 120), ("shoulder_hip_angular_speed_drop_deg_s", "gte", 5)],
            "FS09-M05": [("body_speed_std_torso_s", "lte", .3), ("supported_low_motion_duration_ms", "gte", 120), ("successor_interval_ms", "between", [0, 500])],
        }
        result = evaluation("temporal_" + code.lower().replace("-", "_"), [c(k, op, v, METRICS[k]["definition"]) for k, op, v in checks[code]])
        result.update(engine="rallymate_scoring.temporal_card_evidence.compute_temporal_card_evidence",
                      engine_version="temporal-card-evidence-v1.0.0", requires_joint_subwindow=True)
        if code == "FS09-M04":
            for condition in result["conditions"]:
                condition["required_joint_names"] = FEET
        return result
    if code == "FS01-M01":
        return evaluation("head_stability_only", [c("head_relative_motion_range_torso", "lte", .15, "头部相对肩中点的移动幅度有限")], limitation="无活动球/对手触球证据时仅评价原文允许的头部稳定；不评价真实注视和垫步时机。")
    if code in {"FS02-M02", "FS03-M04", "FS04-M02", "FS05-M03", "FS05-M04", "FS10-M02", "FS10-M03"}:
        return evaluation("continuous_translation_proxy", MOVE, limitation="只评价稳定相机下二维位移与姿态；目标方向/场地前后轴/合理回位路线需球场与活动球证据。")
    if code in {"FS02-M03", "FS03-M02"}:
        return evaluation("leg_extension_motion_proxy", [c("left_knee_flexion_deg.range", "gte", 4, "左膝存在可分辨屈伸变化"), c("right_knee_flexion_deg.range", "gte", 4, "右膝存在可分辨屈伸变化"), c("hip_displacement_torso", "gte", .15, "身体发生移动")], limitation="只评价腿与身体运动；不确认支撑侧受力、交替发力次序或支撑早于离地。")
    if code == "FS03-M03":
        return evaluation("continuous_cross_step", [c("ankle_order_exchange_count", "gte", 1, "完整连续步周期内至少一次可分辨投影换序"), c("ankle_max_displacement_torso", "gte", .3, "迈步位移超过微小摆动")], limitation="只评价连续画面横向换序和躯干归一化步幅；未证明原文移动轴、实际轨迹交叉、步幅/髋宽比与平衡全部条件。步周期缺失不能通过，端点弦交叉不等于连续轨迹交叉。")
    if code == "FS04-M03":
        return evaluation("continuous_non_cross_step", [c("ankle_order_exchange_count", "eq", 0, "完整迈步周期双踝没有换序"), c("ankle_max_displacement_torso", "between", [.1, .8], "存在有限迈步位移")])
    if code == "FS04-M04":
        return evaluation("continuous_shuffle_follow", [c("ankle_order_exchange_count", "eq", 0, "完整并步周期双踝不换序"), c("ankle_width_contraction_torso", "gte", .1, "先迈步扩宽后跟随脚收窄")], limitation="必须完整先后脚周期，不能用两帧没有交换判并步完成。")
    if code in {"FS02-M04", "FS07-M02", "FS08-M02"}:
        return evaluation("step_displacement", [c("ankle_max_displacement_torso", "gte", .6 if code == "FS08-M02" else .15, "完整步周期内一侧踝产生位移")], limitation="仅评价迈步位移；离地、真实落地及与来球方向一致性仍需足底/球路证据。")
    if code == "FS08-M03":
        return evaluation("one_ankle_stable_during_stride", [c("ankle_stability_ratio", "lte", .3, "较静止一侧踝轨迹明显小于跨步踝"), c("trunk_tilt_deg.range", "lte", 15, "躯干投影无大幅摆动")])
    if code in {"FS01-M04", "FS06-M04", "FS07-M04", "FS08-M04", "FS08-M05"}:
        return evaluation("projected_support_geometry", BALANCE + [c("ankle_distance_over_hip_width.median", "between", [1, 5], "踝距投影具有可分辨支撑宽度")], limitation="投影支撑区间不是足底支撑面；落地同期、开放/闭合/伸展分类均须额外证据。")
    if code in {"FS02-M05", "FS03-M05", "FS04-M05", "FS05-M05", "FS06-M05", "FS07-M05", "FS10-M05"}:
        return evaluation("deceleration_to_ready_proxy", [c("hip_speed_drop_torso_s", "gte", .2, "该绑定阶段内髋中心减速")] + BALANCE, limitation="只评价减速和姿态；接近目标、足底支撑以及后继FS事件连续衔接未单凭低速确认。")
    if code == "FS09-M02":
        return evaluation("braking_motion_proxy", [c("hip_speed_drop_torso_s", "gte", .2, "该绑定制动阶段内髋中心减速"), c("left_knee_flexion_deg.range", "gte", 4, "左膝出现屈伸变化"), c("right_knee_flexion_deg.range", "gte", 4, "右膝出现屈伸变化")], limitation="须人工或专项证据确认制动脚落地时刻，不能把踝低速直接当落地。")
    return evaluation("external_context_or_step_events", [], kind="manual_review", limitation="本卡需要来球/目标/场地、触地或完整高频步周期证据；缺失时保留候选观察并人工复核，不能根据身体坐标补造。")


SOURCES = [
    {"id": "usta-two-hand", "url": "https://www.usta.com/en/home/improve/tips-and-instruction/national/improve-your-tennis-game--the-driving-force-in-your-two-handed-b.html", "title": "USTA: Driving the two-handed backhand", "accessed": "2026-10-22", "supports": "双反由非持拍侧手参与驱动，双手角色有差异；不支持数值阈值或固定阶段时长。"},
    {"id": "usta-one-hand", "url": "https://www.usta.com/en/home/improve/tips-and-instruction/national/improve-your-tennis-game--hitting-a-one-handed-backhand.html", "title": "USTA: Hitting a one-handed backhand", "accessed": "2026-10-22", "supports": "单反击球点相对身体更靠前，应及早启动挥拍；不支持强制统一十阶段时间比例。"},
    {"id": "lta-backhand", "url": "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-tennis-backhand", "title": "LTA: How to hit a tennis backhand", "accessed": "2026-10-22", "supports": "准备、转体定位、身前触球、单/双手不同随挥及恢复准备的技术次序；数值和阶段分界为本项目工程定义。"},
]

BOUNDARY_REVISIONS = {
    "GS02-M03": ("双手保持持拍关系，肩线开始向反手侧转动", "双手共同引拍达到后摆转折，身体建立蹬地前支撑"),
    "GS02-M04": ("双反后摆转折附近，支撑腿开始伸展且髋中心开始前移", "骨盆出现持续向挥拍方向的旋转"),
    "GS02-M05": ("双反骨盆持续向挥拍方向旋转开始", "胸廓向击球方向持续旋转开始；允许与骨盆转动重叠"),
    "GS02-M06": ("胸廓向击球方向持续旋转开始，双手保持持拍关系", "双腕共同进入持续向前加速段"),
    "GS02-M07": ("双腕共同持续向前加速开始", "已跟踪球拍与活动球进入同一接触候选区"),
    "GS02-M08": ("球拍与活动球进入接触候选区", "活动球离开拍面并建立出球轨迹；必须有球拍/球证据"),
    "GS02-M09": ("活动球离开拍面", "双手共同随挥到达末端转折并开始恢复"),
    "GS02-M10": ("双手随挥末端转折且身体开始回收", "身体与双手持拍重新形成准备姿态或下一步伐开始"),
    "GS03-M01": ("可确认对手出球或本拍来球进入可观测区，非持拍手辅助球拍", "本次垫步落地候选已确认"),
    "GS03-M02": ("本次垫步落地已确认", "反手侧肩线开始转动且非持拍手仍辅助拍喉"),
    "GS03-M03": ("反手侧肩线开始转动，非持拍手辅助拍喉", "单反后摆转折且身体支撑建立"),
    "GS03-M04": ("单反后摆转折附近，支撑腿开始伸展且髋中心前上移动", "骨盆向击球方向旋转开始，非持拍手仍辅助球拍"),
    "GS03-M05": ("单反骨盆向击球方向旋转开始", "胸廓向击球方向旋转开始且非持拍手开始解除球拍辅助"),
    "GS03-M06": ("胸廓向击球方向转动，非持拍手开始解除球拍辅助", "持拍腕进入持续向前加速且双手分离"),
    "GS03-M07": ("持拍腕单手持续加速，非持拍手与球拍分离", "球拍与活动球进入身前接触候选区"),
    "GS03-M08": ("球拍与活动球进入身前接触候选区", "活动球离开拍面并建立出球轨迹"),
    "GS03-M09": ("活动球离开拍面", "单手随挥到达末端转折，双臂展开后开始回收"),
    "GS03-M10": ("单反随挥末端转折并开始回收", "非持拍手重新辅助拍喉且身体恢复准备或下一步伐开始"),
    "GS04-M01": ("可确认对手出球或本拍来球进入可观测区，建立切削准备", "本次垫步落地候选已确认，非持拍手辅助拍喉"),
    "GS04-M02": ("切削准备垫步落地已确认", "向反手切削侧第一次转肩开始，接续原文GS04-M03"),
}


def field_text(card, key):
    return card.get("source_fields", {}).get(key, {}).get("text", "")


def anchors(card):
    result = {}
    for key, field in card["source_fields"].items():
        result[key] = {"text": field.get("text", ""),
                       "locators": field.get("locators", [field["locator"]] if field.get("locator") else []),
                       "cells": field.get("cells", [field["table_cell"]] if field.get("table_cell") else [])}
    return result


def stage_rules(reference):
    stages = []
    for source in reference["detailed_GS_stages"]:
        code = source["stage_code"]
        start = source["summary_table"]["开始标志性动作"]["text"]
        end = source["summary_table"]["结束标志性动作"]["text"]
        revised = BOUNDARY_REVISIONS.get(code)
        stages.append({"stage_code": code, "event_code": source["event_code"], "name_zh": source["name_zh"],
            "source": {"start": start, "end": end, "heading": source["heading"], "body_context": source["body_context"]},
            "effective": {"start": revised[0] if revised else start, "end": revised[1] if revised else end},
            "boundary_origin": "engineering_revision" if revised else "source",
            "revision_id": "stage-boundaries-20261022-" + code if revised else None,
            "binding_policy": "Require explicit landmark evidence for this exact stage; never split a three-phase candidate into ten equal intervals.",
            "overlap_policy": "Biological phases may overlap. Validate start < end per stage; do not enforce exclusive equal-length partitions.",
            "truncation_policy": "missing start/end produces phase_unresolved; no extrapolated grade",
            "evidence_sources": ["lta-backhand", "usta-two-hand" if code.startswith("GS02") else "usta-one-hand"] if revised else []})
    for row in reference["formal_event_stage_mappings"]:
        fields = row["fields"]
        if "FS编号" not in fields:
            continue
        code = fields["FS编号"]["text"]
        stages.append({"stage_code": code, "event_code": code, "name_zh": fields["名称"]["text"],
            "source": deepcopy(fields), "effective": {"start": fields["开始标志性动作"]["text"], "end": fields["结束标志性动作"]["text"]},
            "boundary_origin": "source", "revision_id": None,
            "binding_policy": "Bind complete event plus the card-specific continuous subwindow, with person/video/frame identities.",
            "overlap_policy": "FS events can overlap stroke stages; each card retains its own source-frame denominator.",
            "truncation_policy": "missing start/end produces phase_unresolved; no extrapolated grade", "evidence_sources": []})
    return stages


CARD_REVISIONS = {
    "GS01-M10-04": {
        "definition": "收拍后调整躯干与下肢姿态，恢复可继续移动的平衡，双手重新建立球拍控制，准备下一次垫步或启动。",
        "calculation": "连续恢复窗内髋投影支撑、躯干摆动、双腕相对位置及后继FS01/FS02；握拍细节另需球拍证据。",
        "required_points": "双肩、双髋、双膝、双踝、双肘、双腕；球拍与后继事件证据另列",
        "observation_points": "恢复窗内身体姿态、双腕收拢与下一动作衔接；不将双腕接近直接当握拍。",
        "key_items": "恢复身体平衡；双手重新建立球拍控制；调整躯干与下肢姿态；衔接下一次垫步或启动。",
        "source_status": "工程修订：身体准备代理可按有效证据计算；完整握拍与后继衔接需补充证据。",
        "unevaluable": "身份或阶段无法绑定；所需可见关节不足70%；球拍不可用仅评价身体代理，缺后继片段不确认下一拍衔接。",
        "positive_feedback": "本恢复窗的身体与手部准备证据较完整。",
        "improvement_feedback": "检查恢复后的平衡、球拍控制和下一次移动衔接。",
    },
    "GS02-M01-01": {
        "definition": "持续观察来球位置、速度及飞行方向，为双手反手的准备和移动提供视觉信息。",
        "calculation": "头部朝向与活动球来向的关系及连续性只作视觉注意代理；真实眼球注视通过人工或专用眼动证据确认。",
        "required_points": "鼻、双耳；活动球轨迹；真实注视需要眼动或人工可辨证据",
        "observation_points": "头部稳定、头与来球方向关系；不再用双手握拍稳定度作为盯球定义。",
        "key_items": "持续观察活动球飞行；判断来球位置、高度、速度和方向；头部稳定仅作附属证据，真实注视另行确认。",
        "source_status": "工程修订：真实观察/注视需人工或专项证据；头部代理不作为原卡自动等级。",
        "unevaluable": "身份或准备阶段无法绑定；头部有效可见帧不足70%；无活动球时只输出头部稳定，不判注视是否正确；无眼动证据不自动确认真实注视。",
        "positive_feedback": "可复核片段显示观察来球的连续行为。",
        "improvement_feedback": "复核来球期间观察行为是否连续；头部稳定不能单独证明眼睛盯球。",
        "grades": {"A": "观察来球行为完整且连续，有可复核注视证据", "B": "大部分来球过程保持观察，仅短暂中断", "C": "能确认观察来球，但连续性一般", "D": "可见观察明显中断或方向偏离", "E": "在充分可见证据下未观察到该观察行为"},
    },
    "GS02-M01-02": {
        "definition": "建立稳定的双手握拍关系，双手共同控制球拍，形成双手反手准备握拍姿态。",
        "calculation": "双手与拍柄的接触关系及连续性、双腕和双肘相对位置；只有人体点时仅输出双腕接近代理。",
        "required_points": "双肩、双肘、双腕；球拍拍柄/拍喉关键点或人工握拍证据",
        "observation_points": "双手在拍柄上的相对位置与持续接触，不再以活动球追踪作为握拍定义。",
        "key_items": "双手在拍柄建立协调握持；保持手拍接触与合理相对位置；为共同引拍建立控制；身体前方与拍面状态需球拍证据。",
        "source_status": "工程修订：双腕关系仅局部代理；完整双手握拍需可辨手拍接触证据。",
        "unevaluable": "身份或准备阶段无法绑定；手臂可见有效帧不足70%；球拍不可辨时不自动判握拍；真实握力不可由单目测得。",
        "positive_feedback": "本段双手握拍关系稳定，并为双反准备提供控制。",
        "improvement_feedback": "复核双手与拍柄接触是否持续，避免仅靠双腕距离判断握拍。",
        "grades": {"A": "双手握拍关系完整、稳定、连续", "B": "双手控制基本稳定，有轻微调整", "C": "已建立双手握拍但连续性一般", "D": "双手控制明显中断或关系不稳定", "E": "在双手与球拍可辨的证据下未建立双手握拍"},
    },
}


QUALITY = {
    "minimum_valid_fraction": .7, "operator": ">=", "minimum_samples": 3,
    "denominator": "all_expected_source_frames_in_closed_card_window_including_missing_or_occluded_frames",
    "numerator": "frames_where_every_card_required_joint_is_valid_simultaneously",
    "point_validity": "finite normalized coordinates and explicit observation confidence >=0.35; in-image validity requires separate source geometry evidence; model-generated geometry is not verified visibility",
    "in_image_verification": "not established by normalized geometry alone; required before claiming source visibility",
    "minimum_confidence": .35,
    "visibility_semantics": "observed_visibility_and_model_geometry_reported_separately",
    "model_geometry_policy": "allow explicitly labelled visual_proxy only; never claim original 70% visible-joint rule verified",
    "identity": "same source video sha256, person_track_id, selection_epoch and camera_reference_epoch throughout window",
    "occlusion": "keep occluded frames in denominator; no invented interpolation in grade inputs",
    "reassociation": "new track association requires explicit identity proof; do not bridge different people or ambiguous selection",
    "continuity": "strictly increasing source frames and timestamps; missing source-frame gaps split windows; no aggregation across discontinuities",
    "phase_binding": "exact source or revised stage window, never generic preparation/acceleration/follow-through relabelled as ten GS stages",
    "failure_output": "unavailable_or_manual_review_with_null_grade_and_null_score_not_E",
}


def build_catalog():
    reference_bytes = (ROOT / "contracts/scoring-reference-20261004.json").read_bytes()
    reference = json.loads(reference_bytes)
    technical_bytes = (ROOT / "src/rallymate_scoring/data/technical_review_rules.json").read_bytes()
    technical = json.loads(technical_bytes)
    originals = {row["indicator_id"]: row for row in reference["indicators"]}
    stages = stage_rules(reference)
    stage_map = {stage["stage_code"]: stage for stage in stages}
    revisions = []
    for stage in stages:
        if stage["revision_id"]:
            revisions.append({"id": stage["revision_id"], "scope": "stage", "target": stage["stage_code"],
                "before": {"start": stage["source"]["start"], "end": stage["source"]["end"]},
                "after": stage["effective"], "reason": "原文开始/结束为空；以本技术阶段定义和前后阶段语义补齐可复核标志。不是原文数值或原文已有边界。",
                "basis": stage["evidence_sources"], "status": "engineering_definition_pending_video_landmark_validation"})
    revisions.append({"id": "stage-definition-20261022-GS04-M02", "scope": "stage_definition", "target": "GS04-M02",
        "before": stage_map["GS04-M02"]["source"]["body_context"][0]["text"],
        "after": "垫步后根据来球移动并调整步伐，保持非持拍手辅助拍喉，直到首次切削转肩开始。",
        "reason": "原文M02阶段定义重复M03转肩，且与M02第一步/调整/移动五卡及复用GS03-M02关系冲突。按M02各卡语义修订，保留M03原文转肩边界。",
        "basis": ["original_GS04-M02_cards", "original_GS03-M02_stage", "lta-backhand"], "status": "engineering_editorial_revision"})
    stage_map["GS04-M02"]["effective"]["definition"] = revisions[-1]["after"]
    cards = []
    for rule in technical["rules"]:
        indicator = rule["indicator_id"]
        original = originals[indicator]
        effective = {key: deepcopy(rule[key]) for key in ("name_zh", "definition", "calculation", "required_points", "unevaluable", "grades")}
        source_field_keys = {"key_items": "原文关键项", "source_status": "当前状态", "observation_points": "当前识别点", "positive_feedback": "AI正向反馈", "improvement_feedback": "AI改进反馈"}
        effective.update({key: field_text(original, field) for key, field in source_field_keys.items()})
        revision_ids = []
        if indicator in CARD_REVISIONS:
            changes = CARD_REVISIONS[indicator]
            revision_id = "card-conflict-20261022-" + indicator
            revisions.append({"id": revision_id, "scope": "card_fields", "target": indicator,
                "before": {key: effective.get(key, field_text(original, source_field_keys.get(key, key))) for key in changes},
                "after": deepcopy(changes),
                "reason": "保留指标ID及名称，按所属阶段与同卡语义重写错位定义及关键项/识别/计算/所需点/不可评价/状态/反馈，未只交换标题。" if indicator.startswith("GS02") else "移除混入技术定义的下一节标题GS-02双手反手，依据本阶段恢复准备定义重建完整语义。",
                "basis": ["original_stage_definition", "lta-backhand"], "status": "engineering_editorial_revision"})
            effective.update(deepcopy(changes))
            revision_ids.append(revision_id)
        stage_code = rule["event_code"] if indicator.startswith("FS") else rule["stage_code"]
        stage = stage_map[stage_code]
        if stage["revision_id"]:
            revision_ids.append(stage["revision_id"])
        if stage_code == "GS04-M02":
            revision_ids.append("stage-definition-20261022-GS04-M02")
        current = {**rule, **effective}
        assessment = fs_evaluation(current) if indicator.startswith("FS") else gs_evaluation(current)
        conditions = assessment["conditions"]
        required = sorted({name for condition in conditions for name in condition["required_joint_names"]})
        dependencies = sorted({dep for condition in conditions for dep in METRICS[condition["metric"]]["dependencies"]})
        if assessment["kind"] == "manual_review":
            dependencies.append("reviewer_observation_of_source_behavior")
        if any(METRICS[condition["metric"]]["camera_reference_required"] for condition in conditions):
            dependencies.append("stationary_or_compensated_camera_reference")
        cards.append({"indicator_id": indicator, "event_code": rule["event_code"], "stage_code": stage_code,
            "rule_version": VERSION, "source": {"document_id": rule["source_document_id"],
                "document_sha256": rule["source_document_sha256"], "table": rule["source_table"],
                "rubric_sha256": rule["rubric_sha256"], "fields": anchors(original), "grades": rule["grades"],
                "original_blockers": rule["blockers"], "original_warnings": rule.get("warnings", [])},
            "effective": effective, "revision_ids": revision_ids,
            "stage_window": {"stage_code": stage_code, **stage["effective"], "origin": stage["boundary_origin"],
                "binding_required": True, "subwindow_required": assessment.get("requires_joint_subwindow", False)},
            "required_joint_names": required,
            "dependencies": ["source_video_sha256", "continuous_person_identity", "exact_stage_landmarks"] + dependencies,
            "evaluation": assessment, "quality_policy": deepcopy(QUALITY),
            "fallback": {"trigger": ["missing_or_ambiguous_stage_landmark", "insufficient_joint_coverage", "unregistered_metric", "missing_external_dependency"],
                "actions": ["retain_card_specific_partial_measurements", "reassociate_only_with_same_person_evidence", "offer_adjacent_candidate_windows_without_merging_people_or_phases", "manual_review_with_source_anchors"],
                "candidate_only_policy": "local hip dips and low ankle motion may be standing adjustments; fallback candidate action type must be independently verified before any grade. temporal-card-evidence-v1.0.0 fallback reports needs_review with null passed/grade.",
                "score_semantics": "unavailable partial evidence does not become E or zero; fallback proxy must have its own version and complete quality checks"},
            "validation": {"contract_test": "tests/test_card_rule_catalog.py", "runtime_test": "requires registered extractor evidence", "video_evidence": [],
                "status": "specified_not_claimed_video_validated", "automatic_source_grade_implemented": False}})
    return {"version": VERSION, "source_reference_id": reference["reference_id"],
        "source_reference_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "technical_review_rules_sha256": hashlib.sha256(technical_bytes).hexdigest(),
        "policy": {"source_text_immutable": True, "contract_count_is_not_implementation_coverage": True,
            "numeric_thresholds": "provisional engineering sensitivity checks; no coaching calibration or original Word numeric claims",
            "grade_policy": {"id": "condition_completion_v1", "scope": "engineering_proxy_only", "mapping": GRADE_MAPPING,
                "bins": [{"grade": "A", "minimum_fraction": 1}, {"grade": "B", "minimum_fraction": .75}, {"grade": "C", "minimum_fraction": .5}, {"grade": "D", "minimum_fraction_exclusive": 0}, {"grade": "E", "fraction": 0}],
                "all_conditions_must_be_measured": True, "unknown_condition_is_not_failed_condition": True,
                "interpretation": "discrete completion of this card's visual proxy checks, not original technical skill grade"},
            "aggregation": {"default": "arithmetic_mean_of_available_proxy_card_scores", "weight": "one_per_card_engineering_choice", "missing": "excluded_from_score_denominator_and_reported_in_coverage", "no_available_scores": None, "separate_outputs": ["source_grade", "proxy_score", "manual_review", "unavailable"], "expected_denominator": "all applicable source cards, with applicability stated"}},
        "sources": SOURCES, "metrics": deepcopy(METRICS), "stages": stages, "revisions": revisions, "cards": cards}


if __name__ == "__main__":
    output = ROOT / "src/rallymate_scoring/data/card_rules_v1.json"
    data = build_catalog()
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(data['cards'])} cards, {len(data['stages'])} stages, {len(data['revisions'])} revisions to {output}")
