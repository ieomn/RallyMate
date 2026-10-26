# GS M01 局部下降辅助证据 v1

本模块为五张需要人工复核的准备阶段卡增加原帧测量，不增加原卡等级或代理分数。支持卡为 `GS01-M01-03`、`GS02-M01-04`、`GS03-M01-03`、`GS04-M01-03`、`GS05-M01-03`。目录仍为 `manual_review`，`conditions=[]`；正常实例仍为 `review_required`，原阶段无效时仍为 `unavailable`。`original_grade`、`proxy_grade`、`score` 始终为空。历史结果、目录规则及其工程参考值没有修改。

`gs_preload_evidence.py` 版本为 `gs-preload-evidence-v1.0.0`。它仅回答一个人工圈定的原帧子窗内，图像髋中点是否向下、双膝屈曲如何变化，以及三项变化是否发生在相同相邻帧区间。局部下降也可能来自站姿调整，不能据此确认垫步、落地、受力、弹性支撑、身体物理重心或技术平衡。没有接入 FS01 事件，更不会复用保存的 FS01 等级或分数。

## 可选标注输入

现有 `gs-stage-annotations-v1.0.0` 文档不变。仅允许在对应 M01 `stage` 中增加可选 `local_preload_observation` 对象；旧文档不含该字段时输出保持原样。对象存在时，以下字段全部必填，其他字段一律拒绝：

- `version`: `manual-gs-local-preload-v1.0.0`。
- `method`: `manual_anchor`。
- `annotation_id`、`action_id`、`person_track_id`: 必须逐项等于所属阶段；阶段本身须通过既有动作、人物、技术和时间验证。
- `indicator_id`: 上述五张卡之一，必须属于该阶段的技术代码和 M01。
- `video_sha256`: 必须等于文档原视频 SHA-256。
- `start_frame`、`end_frame`: 各仅含非负整数 `frame_index`、`pts` 和正整数 `time_base_num`、`time_base_den`。首尾帧编号严格递增，PTS 换算时间严格递增且完整包含在 M01 阶段内。每个字段须逐字匹配原视频实际解码账本；仅换算为相同时间的不同 time base 也不接受。
- `source`、`source_description`: 各 1–512 字符且不能仅为空白，供人工查看来源与可见现象。
- `uncertainty_ms`: 有限非负数，拒绝布尔值；仅展示，不扩展边界或豁免缺帧。
- `semantics`: `manual_local_hip_descent_not_confirmed_split_step`。

调用者不能添加 `quality`、`passed`、`score`、`force`、`split_step_confirmed` 或关联 FS 事件来绕过检查。标注采用人工非专家来源语义；源帧绑定只证明时间与身份关联，不把人工边界变为专家真值。

记录级 `compute_gs_card_execution` 仍返回 `artifact_files_verified=false`。只有既有 `build_gs_card_execution_report` 实际核验原视频、原帧和选人等文件后，父报告的文件验证标记才成立。辅助证据不会自行宣称验证过文件。

## 计算与缺测

子窗是包含首尾的连续原帧集合。必须至少有 7 帧；不补帧、不改首尾、不截掉缺测帧。所有预期解码帧保留在分母、源帧序列和证据中。八个共同必要关节为左右肩、髋、膝、踝；每帧都必须同时满足坐标和置信度要求。70% 联合有效比例仅作为诊断展示，不能替代连续测量所需的逐帧完整性。单目模型观测仍继承已验证模型来源契约，不能用空置信度冒充图像可见性。

沿用 `card_kinematics.validate_window` 的原帧顺序、单调实际 PTS、时间连续性、归一化、同人物、同选人 epoch 和同相机参考 epoch 门槛。相机补偿及固定参考缺失、原阶段不可用、任一原帧或必要关节缺失、膝角退化、不到 7 帧时，辅助状态为 `unavailable`，三项汇总值和联合下降比例均为空。保留下来的坐标仅供诊断，不能据此声称连续测量有效。

三项数值由 `measure_card_window` 计算，复用 GS 端点中位数语义：首尾各取 `max(3, ceil(原帧数 × 20%))` 帧；固定躯干尺度为本子窗各帧肩中点至髋中点距离的中位数。髋下降是固定相机参考中末端髋中点 y 中位数减起端中位数，再除以固定躯干尺度。双膝值分别是末端屈曲角中位数减起端屈曲角中位数。它们不是首尾单帧差，也不是整个 M01 阶段的端点差。

目录中的辅助工程参考值原样保留：髋下降 `0.03 torso`、左膝增加 `4 deg`、右膝增加 `4 deg`。输出不判定参考值是否达标，`passed=null`、`scoring_eligible=false`。目录三项定义或参考值改变会明确拒绝复用此版本引擎。

`joint_descent_fraction` 为三项在同一对相邻原帧中严格增加的实际 PTS 时长之和，除以子窗首尾实际 PTS 时长。三项分别是图像髋中点 y、左膝屈曲角和右膝屈曲角。静止不算下降；不存在按三条独立好窗口拼接的通过结果。此比例无评分阈值，也不采用区间数量占比替代时长占比。

## 输出契约

只有指定卡的 `instance.auxiliary_evidence` 增加该证据，其余卡实例没有该字段。核心字段如下：

- `version`、`measurement_version`、`status`（`measured_auxiliary` 或 `unavailable`）、`semantics`、`window`、`reason_codes`。
- `original_grade=null`、`proxy_grade=null`、`score=null`、`source_grade_supported=false`、`split_step_confirmed=false`、`interpolation_used=false`。
- `auxiliary_observations`: 三项 `{metric, unit, value, engineering_reference: {operator: "gte", value}, passed: null, scoring_eligible: false}`。
- `joint_descent_fraction` 和 `joint_descent_semantics`，后者固定为 `duration_weighted_adjacent_intervals_with_hip_y_and_both_knee_flexions_increasing`。
- `quality`: 八关节清单、原窗总帧数、联合有效帧数及比例、70% 诊断标志、连续完整标志、各关节有效源帧列表；`visibility_verified`、`image_bounds_verified` 均为 false。分母标记为 `all_expected_decoder_frames_in_closed_local_window`。
- `source_frame_bindings`: 与整个原窗逐帧对齐，含 `frame_index`、实际 `pts`、`time_base_num`、`time_base_den`、`video_sha256`、原观测 `timestamp_ms`、`selected_person_track_id`、`selection_epoch`、`camera_reference_epoch`。缺原帧时保留实际解码行，后三个身份或参考字段为空。
- `series`: `source_frames`、由实际 PTS 得出的 `source_timestamps_ms`、`hip_y_camera_reference`、`left_knee_flexion_deg`、`right_knee_flexion_deg`、`torso_scale_by_frame`、`fixed_torso_scale`、`endpoint_frame_count`、`pair_source_frames`、`simultaneous_descent_intervals`。像素坐标基准为 `camera_reference_pixels`；缺测保留 null。
- `source_annotation`: 完整可选输入 `observation`、整个父标注文档的 `document_version` 和 `document_sha256`、`digest_kind=canonical_json_sorted_utf8_v1`、`stage_code`、`stage_window_ms`、`expert_adjudicated=false`、`file_verification=parent_report_input_flags`。摘要采用排序键、无多余空白的 UTF-8 JSON（不转义非 ASCII）；不是原文件字节摘要。

父报告的规则版本、来源核验、评分汇总与卡片状态仍遵守原 GS 契约。新增字段仅是后端证据接口；前端展示和导出适配不在本批范围。

## 当前验证边界

本批没有真实 M01 案例，真实辅助测量实例数为 **0**。已提交 MIT 真实准备阶段仍为 M03，前挥阶段仍为 M07；没有改名或为满足 7 帧延长窗口。

`tests/test_gs_preload_evidence.py` 的完整下降、局部下降、错时下降及 7 帧边界都是合成数学测试。它们验证端点中位数、非零 PTS 原点、实际时长权重、原窗分母、绑定和空分数，不构成真实网球阳性或专家标注。既有真实 M03/M07 回归仅验证新可选字段缺省时行为保持一致。
