# FS03 / FS04 原视频逐卡服务 v1

`compute_footwork_cycle_execution` 将原始姿态重新计算为 10 张卡的实例。8 张二维代理卡仅在完整窗口和全部条件可计算时生成工程条件完成度；两张 M01 仍需要来球与目标方向外部证据。任何原文等级都为 null，原文自动评分数为 0，工程代理阈值未经专家标定。

`build_footwork_cycle_artifact_report` 是正式文件入口，依次核对服务指定的 run 目录、summary job ID、四个源文件的 SHA-256、summary 中的原视频与帧文件散列，并重新解码原视频取得整数 PTS。随后核对每条原帧、选择记录、相机参考和人工窗口边界。输入中的分数、通过标记、质量标记与替代轨迹都不是标注文档允许的字段。

## 人工窗口文件

服务仅从 run 目录读取可选的 `footwork-cycle-annotations.json`。文档包含：

- `version="footwork-cycle-annotations-v1.0.0"`、`source_run_id`、`video_sha256`。
- `input_artifact_sha256`：`frames.jsonl`、`primary-player.jsonl`、`events.jsonl`、`summary.json` 四个原文件的字节散列。
- `windows`：每个窗口含唯一 `event_id`、`event_code`（FS03 或 FS04）、`primary_player_id`、`start_source_frame`、`end_source_frame`、`start_ms`、`end_ms`。
- 每个窗口的 `binding` 包含 `method="manual_anchor"`、非空 `version` 与 `source`、非负有限 `uncertainty_ms`，以及 `semantics="manual_visual_candidate_window_not_expert_technique_or_contact_truth"`。

时间必须与实际解码的首尾源帧相符，容差仅为原导出毫秒取整的 0.51 ms。首、尾或内部任一帧缺失都会拒绝该文件的周期执行，不能将窗口缩短后重新计算分母。相同人的重叠窗口不能同时标成两个动作家族。人工候选不代表技术类别、足底触地、支撑力或专家技术质量真值。

## 原始证据与异常隔离

关节坐标、模型请求、已绑定的原导出时间和身份 epoch 不变。缺失时钟字段可以从核验后的原视频补入；未提供身份 epoch 时，优先保留完整核验通过的原模型绑定，否则明确由原始选人和相机切换记录派生。该派生只代表算法选择的连续段，不代表人工确认了身份。速度始终使用整数 PTS / timebase，不能使用取整毫秒估算。

每个候选窗口保留所有相交原事件的质量诊断。同一原事件 ID 的相同记录去重，冲突记录拒绝。若任一跳点或换侧诊断缺少完整关节名单，不能用其他记录的完整名单替它解除复核。输出保留质量来源事件 ID、原帧 PTS / 选择 / 相机绑定、人工标注文档散列与边界不确定性。

记录级入口始终标记 `artifact_files_verified=false`、`decoder_verified_from_original_video=false`、`service_eligible=false`。只有正式入口核验实际文件和原视频、并在计算后再次核对散列，才将三项置为 true。统一报告只接受这三个条件成立的结果，替换 FS03/FS04 的 10 行并重新汇总全部 298 行。该模块失败时只将这 10 行标为 unavailable，保留其他 FS 与 GS 结果。

可选文件存在时，其内容、四个源文件和原视频内容都进入缓存键；大小与修改时间未变的编辑也必须重算。没有该文件时，原有五张时序 FS 卡和可选 GS 阶段行为保持一致。

## 验证边界

便携测试覆盖跨 run、错误视频、缺首尾与内部帧、重复与冲突原事件、同人重叠窗口、伪造分数、未注册检测器、质量来源不完整、SAM 原取整时间与 epoch 保留、异常隔离及缓存内容变化。用于服务接线的 MIT 窗口是明确的测试投影，不作为并步/交叉步真值或真实正例覆盖计数。

正式原视频回放使用 USTA 回位片段的 539 帧与 6 个人工候选窗口，仅投影到 FS04，共 30 个实例。原文件未修改；所有实例都需要复核，没有工程分数或原文等级。该结果证明证据读取和拒绝路径可复现，不能证明 6 段都是并步，也不能证明周期识别准确率。完整媒体与报告只保留在未跟踪的本地验证目录。
