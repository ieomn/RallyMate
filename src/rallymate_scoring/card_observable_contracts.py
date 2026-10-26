"""Explicit unresolved-observable map, separate from the immutable card rules.

Every manual catalogue card is assigned by ID, not by a name-keyword heuristic.
An available correlated measurement does not establish the source predicate.
"""
from __future__ import annotations

from copy import deepcopy
from .card_rule_catalog import load_card_rules

VERSION = "card-observable-contracts-v1.0.0"

# Required observations, current evidence and missing implementation are kept
# separately so a registered adapter never masquerades as completed behavior.
PREDICATES = {
 "gaze_ball": ("眼睛持续注视活动球", ["eye_gaze_direction", "active_ball_identity_trajectory", "synchronized_pts"],
    ["head_relative_motion_range_torso:registered", "ball_boxes:recorded_if_detected"], "眼朝向/注视与活动球关联没有输入；鼻点或头稳定不是注视", "new_observation_source_required"),
 "shoulder_hip_separation": ("肩髋投影分离", ["shoulders_hips_common_geometry", "exact_stage_window"],
    ["shoulder_hip_separation_deg:registered"], "对应复合原卡含盯球；现有分离量尚未独立作为该卡消费者", "existing_numeric_algorithm_not_connected_to_this_predicate"),
 "local_preload": ("同一局部窗髋下降及膝屈曲", ["hips_knees_ankles_shoulders_common_geometry", "contained_preload_window", "exact_pts"],
    ["gs_preload_evidence:bound_auxiliary_runtime"], "已有8关节共同分母辅助量；不证明真实重心或完整垫步，本原卡未接评分条件", "partial_numeric_evidence_already_exists"),
 "split_step": ("完整垫步离地及落地时序", ["both_feet_contact_state", "takeoff_landing_timing", "exact_gs_stage_containment"],
    ["temporal_card_evidence:local_dip_and_landing_geometry"], "局部髋低点和低踝速度不能确认双脚离地或落地", "new_observation_source_required"),
 "first_step": ("在本阶段首次启动并形成有方向的身体位移", ["complete_ankle_motion", "preceding_still_inventory", "direction_reference", "gs_stage_fs_window_relation"],
    ["step_motion_evidence:four_fs_cards", "direction_reference_observables:independent_source_contract"], "仍缺GS与首个FS完整窗绑定及此前无更早启动证明；只移植分数会重复/错序", "temporal_input_and_consumer_missing"),
 "adjustment_steps": ("连续小步与步幅/频率调整", ["multiple_complete_ankle_motions", "whole_stage_pts", "bounded_step_amplitude", "direction_reference"],
    ["step_motion_evidence:single_cycle_geometry"], "已有单次运动；连续小步序列/频率和GS阶段消费者缺失", "new_numeric_sequence_algorithm_required"),
 "racket_path": ("球拍自身的连续二维轨迹", ["racket_identity_bound_to_player", "racket_keypoint_or_defined_reference_point", "exact_pts"],
    ["racket_boxes:available_if_detected", "trajectory_preview:bounding_box_track_only"], "未定义并跟踪拍头/拍柄点；框中心随姿态变动，不是拍头或控制点", "specialized_observation_and_algorithm_missing"),
 "racket_body_direction": ("球拍相对身体前/侧/后及切削方向", ["racket_path", "source_body_frame_or_court_axis", "exact_stage_window"],
    ["pose_reference_geometry:available", "racket_boxes:available_if_detected"], "前后深度与拍头轨迹未恢复；二维腕点不能代拍头", "specialized_observation_and_algorithm_missing"),
 "racket_height": ("拍头高位或持续上升/下降", ["racket_head_keypoint", "source_camera_reference", "source_pts"],
    ["racket_boxes:available_if_detected"], "框顶/框中心不是拍头；缺专用拍头检测及同源数值提取器", "specialized_observation_and_algorithm_missing"),
 "racket_lag_release": ("球拍相对持拍手滞后与释放", ["racket_handle_and_head", "observed_measurement_wrist_side", "wrist_racket_relative_angles_over_pts"],
    ["manual_racket_side_observation:source_bound", "wrist_speed_increase_torso_s:registered"], "缺球拍轴/腕拍夹角数值；腕速不能代球拍滞后或释放", "specialized_observation_and_algorithm_missing"),
 "racket_hand_control": ("指定手持续握持并控制球拍", ["hand_racket_contact_identity", "continuous_hand_racket_relation", "racket_trajectory"],
    ["manual_racket_side_observation:sampled_frames_only"], "少数帧持拍观察不证明整窗持续握持、握力或控制", "new_observation_source_required"),
 "racket_face": ("拍面打开或保持受控", ["racket_face_orientation", "racket_handle_head_geometry", "source_camera_model"],
    ["racket_boxes:available_if_detected"], "检测框不确定拍面法向；缺专用点或多视角，未有数值算法", "specialized_observation_and_algorithm_missing"),
 "ball_contact": ("球拍与活动球实际接触/分离", ["active_ball_identity", "racket_contact_surface", "contact_time_evidence"],
    ["ball_racket_boxes:possible_cooccurrence"], "框接近不证明接触；缺足够时间分辨率的接触证据", "new_observation_source_required"),
 "impact_target": ("球拍接近预计或适宜击球区域", ["source_bound_target_region", "ball_body_racket_trajectory", "validated_prediction_or_observed_region"],
    ["trajectory_preview:constant_velocity_display_only"], "显示外推不授权预测击球点；缺目标定义、误差和球拍关键点", "external_context_and_algorithm_missing"),
 "force": ("主动蹬地或地面反作用力", ["foot_ground_contact", "force_measurement_or_validated_inverse_dynamics"],
    ["knee_flexion_and_hip_motion:registered_geometry"], "角度/速度不等于受力；单目无质量/接触/外力闭合", "physical_observation_required"),
 "energy_transfer": ("下肢/躯干到上肢的力能传递", ["segment_mass_inertia", "three_dimensional_motion", "external_forces_and_contact"],
    ["projected_segment_angles:registered"], "投影角度先后不足以测力或功；未实现能量数值模型", "physical_observation_required"),
 "muscle_activation": ("背部肌群持续发力", ["muscle_activation_or_validated_force_observation"],
    ["back_posture:pose_geometry_only"], "姿态不证明肌肉收缩/激活", "physical_observation_required"),
 "ball_spin": ("球离拍后下旋", ["resolved_ball_surface_rotation", "exact_pts", "spin_axis_and_rate"],
    ["ball_center_boxes:available_if_detected"], "球心轨迹不确定自转；缺球面纹理/旋转输入", "specialized_observation_and_algorithm_missing"),
 "side_loading": ("向持拍侧加载", ["observed_racket_side", "support_foot_identity", "hip_relative_support_change", "load_measurement"],
    ["manual_racket_side_observation:sampled", "hip_projection_displacement:registered"], "侧别可观察但承重不可由位移推出；尚无该卡侧向几何组合", "partial_geometry_consumer_and_physical_source_missing"),
 "rotation_preparation": ("阶段末形成骨盆/胸廓启动准备状态", ["source_bound_reference_posture", "stage_transition_timing", "segment_orientation"],
    ["shoulder_hip_axis_angles:registered"], "当前变化量不证明准备姿态；缺参考状态和跨阶段启动边界", "reference_and_temporal_consumer_missing"),
 "pre_acceleration": ("持拍手加速前的准备姿态", ["observed_racket_side", "wrist_elbow_shoulder_pose", "racket_state", "pre_acceleration_boundary"],
    ["elbow_angle_and_wrist_geometry:registered"], "M06结束才加速；缺准备姿态范围/球拍状态，不能套M07腕加速", "reference_and_temporal_consumer_missing"),
 "sideways_maintenance": ("保持先前转肩后的侧身状态", ["source_bound_sideways_reference", "shoulder_hip_orientation", "continuous_posture"],
    ["shoulder_hip_angle_range:registered"], "小范围变化只证明不动，不证明侧身；缺可核验参考侧身姿态", "reference_input_and_consumer_missing"),
 "ball_direction": ("判断或跟随来球/目标方向", ["active_ball_identity_trajectory", "source_bound_movement_axis", "target_region"],
    ["ball_boxes:available_if_detected", "direction_reference_observables:image_axis_only"], "图像运动方向可算；判断本身不可观察，球路/目标绑定未接入这些卡", "external_context_and_consumer_missing"),
 "ball_body_distance": ("判断身体与预计击球点距离", ["active_ball_position_height", "source_bound_predicted_impact", "body_relation"],
    ["manual_visible_ball_center:optional_new_reference"], "单帧可见球位置不是预测击球点或合理距离", "external_context_and_algorithm_missing"),
 "stance_target": ("选择开放/闭合/跨步支撑位置", ["source_bound_impact_target", "court_or_body_direction", "feet_stance_geometry", "contact_state"],
    ["ankle_hip_geometry:registered", "manual_court:dirty_display_only"], "未定义目标与支撑方向；双踝画面展开不等于开放站位", "external_context_and_algorithm_missing"),
 "support_contact": ("指定脚落地并承担支撑", ["target_relative_foot_identity", "foot_ground_contact", "load_distribution"],
    ["ankle_speed_and_hip_support_projection:registered"], "低踝速与髋在踝间不证明触地/承重", "physical_observation_required"),
 "stance_relation": ("开放或闭合前后脚站位", ["independent_source_direction", "both_feet_relative_geometry", "body_orientation_reference"],
    ["hip_ankle_projected_geometry:registered"], "缺真实前后/横向参考，尚无对应有方向站位提取器", "reference_input_and_numeric_algorithm_missing"),
 "inertia_direction": ("识别身体移动与制动方向", ["source_bound_hip_velocity_vector", "body_mass_center_proxy", "braking_timing"],
    ["hip_displacement_speed_and_temporal_braking:registered"], "有标量减速；尚无同窗运动方向与反向制动消费者，不证明心理判断", "existing_geometry_requires_vector_consumer"),
 "tactical_recovery": ("合理回位目标与到达位置", ["court_metric_position", "opponent_position", "ball_route", "declared_tactical_policy"],
    ["manual_court:dirty_display_only", "hip_displacement:registered"], "不能以回到画面中心代合理战术位置；缺策略与度量场地输入", "external_context_and_algorithm_missing"),
 "directional_small_step": ("沿指定投影轴的小幅完整步与可见目标误差变化", ["complete_ankle_motion", "independently_observed_image_axis", "visible_target_if_scoring", "full_source_pts"],
    ["direction_reference_observables:new_numeric_core", "directional_micro_adjustment:new_two_card_service"], "新增应用条件不复原球场度量或预计击球距离；实际原视频验证另列，不能由注册宣称", "new_numeric_and_consumer_implemented_real_evidence_pending"),
}

MANUAL_CARD_PREDICATES = {}


def _assign(ids, *predicates):
    for identifier in ids.split():
        if identifier in MANUAL_CARD_PREDICATES:
            raise ValueError("duplicate predicate mapping: " + identifier)
        MANUAL_CARD_PREDICATES[identifier] = predicates


_assign("GS01-M01-02 GS01-M02-03 GS02-M01-01 GS02-M02-03 GS02-M03-03 GS02-M04-05 GS02-M05-05 GS02-M06-05 GS02-M07-03 GS02-M08-03 GS02-M09-05 GS03-M01-02 GS03-M02-03 GS03-M03-02 GS03-M06-02 GS03-M08-02 GS04-M01-02 GS04-M02-03 GS04-M03-02 GS04-M04-04 GS04-M05-05 GS04-M06-05 GS05-M01-02 GS05-M02-03 GS05-M03-02 GS05-M04-04 GS05-M05-05 GS05-M06-05", "gaze_ball")
_assign("GS01-M05-04", "shoulder_hip_separation", "gaze_ball")
_assign("GS01-M01-03 GS02-M01-04 GS03-M01-03 GS04-M01-03 GS05-M01-03", "local_preload")
_assign("GS01-M01-04 GS03-M01-04 GS04-M01-04 GS05-M01-04 FS01-M03", "split_step")
_assign("GS01-M02-01 GS02-M02-01 GS03-M02-01 GS04-M02-01 GS05-M02-01", "first_step", "ball_direction")
_assign("GS01-M02-02 GS02-M02-02 GS03-M02-02 GS04-M02-02 GS05-M02-02", "adjustment_steps", "ball_direction")
_assign("GS01-M03-03", "racket_path", "racket_body_direction")
_assign("GS01-M06-05 GS01-M07-02 GS01-M07-03 GS02-M07-02 GS03-M07-02", "racket_lag_release")
_assign("GS01-M09-03 GS02-M09-02", "racket_path")
_assign("GS03-M06-05 GS03-M09-01", "racket_hand_control", "racket_path")
_assign("GS04-M03-05 GS05-M03-05", "racket_body_direction", "racket_height")
_assign("GS04-M04-05 GS04-M05-01 GS05-M04-05 GS05-M05-01 GS04-M09-02 GS05-M09-02", "racket_height")
_assign("GS04-M06-02 GS05-M06-02", "racket_height", "impact_target")
_assign("GS04-M06-03 GS04-M07-03 GS05-M06-03 GS05-M07-03 GS04-M10-03 GS05-M10-03", "racket_body_direction")
_assign("GS04-M07-01 GS04-M09-01 GS05-M07-01 GS05-M09-01", "racket_hand_control", "racket_body_direction")
_assign("GS01-M03-04", "side_loading")
_assign("GS01-M04-01 GS02-M04-01 GS03-M04-01 GS01-M04-04 GS03-M04-04", "force")
_assign("GS01-M05-03 GS01-M08-04 GS02-M05-03 GS02-M06-03 GS03-M05-03", "energy_transfer")
_assign("GS03-M09-04", "muscle_activation")
_assign("GS04-M08-04 GS05-M08-04", "ball_spin")
_assign("GS01-M04-06 GS01-M05-06 GS03-M05-05", "rotation_preparation")
_assign("GS01-M06-03", "pre_acceleration")
_assign("GS01-M07-05 GS01-M08-02 GS04-M07-05 GS05-M07-05", "impact_target", "racket_path")
_assign("GS01-M08-03 GS01-M08-05", "ball_contact")
_assign("GS02-M08-02 GS04-M06-01 GS04-M07-02 GS04-M08-02 GS04-M09-03 GS05-M06-01 GS05-M07-02 GS05-M08-02 GS05-M09-03", "racket_face")
_assign("GS04-M05-02 GS05-M05-02", "racket_height", "impact_target")
_assign("GS04-M08-01 GS05-M08-01 GS02-M08-01 GS03-M08-01", "racket_hand_control", "ball_contact")
_assign("GS04-M08-03 GS05-M08-03", "racket_body_direction", "ball_contact", "racket_face")
_assign("GS04-M04-02 GS04-M05-04 GS05-M04-02 GS05-M05-04", "sideways_maintenance")
_assign("FS02-M01 FS03-M01 FS04-M01", "ball_direction")
_assign("FS05-M01", "ball_body_distance")
_assign("FS05-M02", "adjustment_steps")
_assign("FS06-M01 FS07-M01 FS08-M01", "stance_target")
_assign("FS06-M02", "support_contact")
_assign("FS06-M03 FS07-M03", "stance_relation")
_assign("FS09-M01", "inertia_direction")
_assign("FS10-M01 FS10-M04", "tactical_recovery")
_assign("FS05-M03 FS05-M04", "directional_small_step", "ball_body_distance")


def unresolved_card_observables():
    """Return every manual card's exact source anchor and unresolved predicates."""
    cards = [card for card in load_card_rules()["cards"] if card["evaluation"]["kind"] == "manual_review"]
    if set(MANUAL_CARD_PREDICATES) != {card["indicator_id"] for card in cards}:
        raise ValueError("manual card predicate map must cover the exact catalogue inventory")
    result = []
    for card in cards:
        source = card["source"]
        predicates = []
        for identifier in MANUAL_CARD_PREDICATES[card["indicator_id"]]:
            name, required, available, missing, status = PREDICATES[identifier]
            predicates.append({"predicate_id": identifier, "meaning": name, "required_observables": list(required),
                "current_sources": list(available), "implementation_gap": missing, "status": status,
                "source_behavior_fully_implemented": False})
        result.append({"indicator_id": card["indicator_id"], "name_zh": card["effective"]["name_zh"],
            "source": {key: source[key] for key in ("document_id", "document_sha256", "table", "rubric_sha256")},
            "source_fields": {key: deepcopy(source["fields"][key]) for key in ("技术定义", "计算方式", "所需点", "不可评价") if key in source["fields"]},
            "effective_definition": card["effective"]["definition"], "catalogue_family": card["evaluation"]["family"],
            "predicates": predicates, "automatic_original_grade_supported": False})
    return result
