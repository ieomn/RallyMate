# FS 单卡人工源窗口接入

`fs-card-windows-v1.0.0` 为既有连续 FS 五卡和 FS03/FS04 十卡之外的 35 张步伐卡增加正式入口。它将人工逐帧观察的时间范围绑定到原视频、原始姿态和主球员记录，然后重新计算卡片条件。它没有增加自动动作识别器，也不把人工时间标签视为专家技术、地面接触或支撑力真值。

## 卡片范围与计分边界

35 张卡中有 20 张已登记视觉代理条件、15 张保留人工复核。所有实例的 `original_grade` 为 `null`，`source_grade_supported` 为 `false`。只有可计算且通过全部来源和质量检查的视觉代理条件，才可产生工程代理完成度；数值阈值未经教练校准。

FS02-M04、FS07-M02、FS08-M02、FS08-M03 的踝部测量依赖完整步周期。此入口不接受调用者提供的 `step_cycle_binding`，也不从整段动作的两个端点构造周期，故这四卡仍可能因 `complete_step_cycle_not_bound` 不可用。原卡中的离地、触地、来球方向及真实承重均未由此得到确认。

人工可观察卡也先核验窗口来源，但不填入姿态无法恢复的观察或评分。缺失、不可用和未执行都不是 E 或 0 分；不生成混合原等级与代理分的总分。

## 可选输入文件

统一逐卡服务在原运行目录中读取 `fs-card-window-annotations.json`。文件采用 `fs-card-window-annotations-v1.0.0`，顶层只接受：

- `version`、`source_run_id`、原视频的 `video_sha256`。
- `input_artifact_sha256`：`frames.jsonl`、`primary-player.jsonl`、`events.jsonl`、`summary.json` 四个原文件的原始字节 SHA-256。
- `windows`：最多 2000 个单卡窗口。空列表合法，不生成任何实例。

每个窗口只接受 `annotation_id`、`indicator_id`、原始选中轨迹的 `person_track_id`、`start_source_frame`、`end_source_frame`、`start_ms`、`end_ms` 和 `binding`。`binding` 只接受 `method: manual_anchor`、非空 `version`、非空 `source`、非负有限 `uncertainty_ms` 及固定语义 `manual_card_window_not_expert_technique_contact_or_force_truth`。

首尾帧必须存在于实际原视频解码序列，时间与原 PTS 的差异最多为四舍五入容差 0.51 ms。窗口内每个原帧都必须保留；少一个首帧、尾帧或中间帧都不能缩小分母。显式异源的主球员记录、重复 ID、同卡重叠窗口、互相冲突的步伐类别窗口及外部等级、质量豁免、通过标志等字段会被拒绝。

## 身份、原始质量与共同分母

姿态使用原始轨迹编号，检测事件使用虚拟主球员编号。两者的整数碰巧相同不能证明是同一人。原事件必须通过 `track_diagnostics.source_track_ids` 明确绑定窗口中选中的原始轨迹，且身份不模糊、无切换候选。窗口内每个实际时间点都须有匹配原事件的质量记录覆盖；缺少记录、只有很短的片段或仅有另一条轨迹的记录，均不能默认质量良好。

所有重叠事件的全局身份风险会保留。关节跳变和左右交换只有在诊断关节均为已注册名称，且能证明与该卡必要关节无关时，才可局部忽略。未知关节名不构成忽略依据。相机参考段、选人段变化和原 PTS 不连续也会阻断相应结果。

复合条件使用该卡必要关节的共同有效帧。完整原视频窗口始终是分母；70% 几何门槛不等于可见性确认。动态测量继续要求完整连续窗口，不插值、不延长人工窗口来满足最少样本数。

## 输出和统一报告

每个实例保留 `source_window_annotation`：原窗口标签、人工来源、不确定性、原视频和人物、首尾帧、请求时间及整个标注文档的规范化 JSON 摘要。

`source_window_evidence` 保留：

- 全部 `source_frames`、原始 `source_pts` 和 `source_frame_bindings`，包含精确时基、选人段与相机参考段。
- 每条原始事件的时间范围、源轨迹、身份诊断、完整 `source_track_diagnostics`、原始质量标记、是否适用于选中人物及本卡处理结果。明确属于其他人物的重叠记录也保留，便于消费者独立核验。
- `interpolation_used: false`、`step_cycle_bound: false`、`source_behavior_or_contact_verified: false`。

记录级函数 `compute_fs_card_windows` 仅作重放，三个正式验证标志始终为 `false`。只有 `build_fs_card_window_report` 完成目录/任务绑定、四个原文件、原视频、实际 PTS 解码及执行后再次校验，才能标记 `artifact_files_verified`、`decoder_verified_from_original_video` 和 `service_eligible`。

统一服务只替换这 35 张卡，保留既有五卡、FS03/FS04 和 GS 结果。坏的可选标注只使所属卡不可用。缺少标注时原路径行为不变。标注、原始文件和视频内容都进入缓存签名，修改内容但保留文件大小和修改时间也会使缓存失效。

## 验证说明

`tests/test_fs_card_windows.py` 逐卡检查 35 张卡的处理、共同原帧分母、边界/身份/PTS、原事件质量覆盖、不可伪造的周期门槛及读前读后来源检查。这里复用姿态的标签以及受控重复姿态只测试接口约束，不是新增真实技术案例。

`tests/test_card_execution_fs_windows.py` 检查统一报告的限定合并、异常隔离、汇总重算和内容缓存失效。原视频验证另记录冻结的逐帧观察、原图摘要和实际正式入口输出，避免将接口测试计入真实视频覆盖。

真实 USTA 开发材料的三个窗口及回放见 [真实窗口记录](fs-card-real-windows-20261022.md)：6、6、11 个原帧分别得到一个不可用、两个需复核，全部空分。正式 HTTP 接口也已在干净 `2f00a4d` 加本次两个运行模块的候选环境中返回 200，298 行逐卡内容、来源和汇总与直接正式入口报告完全相同；执行所加载的 134 个项目模块均核验了来源。该检查使用既有原始姿态并实际解码原视频，没有声称重新运行姿态模型。

本地 HTTP 证据位于 `.codex_tmp/fs-card-windows-release-20261022/actual-api-card-report.json` 与 `actual-api-validation.json`。新接口、既有 GS/周期和逐卡合并的组合测试为 72 项，66 项通过，6 项因干净目录缺少旧测试约定的媒体路径显式跳过；另行指定真实来源的三段 GS 持拍侧生产入口测试已执行通过。新增 USTA 数值与质量回归独立执行，不将上述跳过项计为通过。
