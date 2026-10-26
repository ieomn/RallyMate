# GS 阶段锚点执行适配器 v1

`rallymate_service.gs_card_execution.build_gs_card_execution_report(run_dir, annotation_document, *, video_path=None)` 是正式文件入口。它读取原始 `frames.jsonl`、`primary-player.jsonl`、`summary.json`，重新解码原视频 PTS，再调用已注册的 `card_kinematics` 和 `card_evaluation`。输入不包含已计算条件、通过标志、等级或分数。

正式入口要求目录名、`summary.job_id`、注释 `source_run_id`、每条 `frames.job_id` 一致。`run_dir` 和可选 `video_path` 应由服务根据请求任务选择，注释不能指定文件路径。视频 SHA256、三个源文件 SHA256 在读取前验证，计算后再次核对，以防读取过程中源文件改变。帧的原始序号、实际解码 PTS、人物选择、选择连续区间、相机参考区间均参与校验。缺失观测保留在完整帧窗口的分母中。

## 注释协议

顶层版本为 `gs-stage-annotations-v1.0.0`，严格字段为：

- `version`、`source_run_id`、`video_sha256`。
- `input_artifact_sha256`：`frames.jsonl`、`primary-player.jsonl`、`summary.json` 的原文件字节摘要。
- `actions`：每项有 `action_id`、`technique_code`、`person_track_id`、`start_ms`、`end_ms`。
- `stages`：每项有 `annotation_id`、`action_id`、`technique_code`、`stage_code`、`person_track_id`、`start_ms`、`end_ms`、`binding`。

`stage_code` 必须是目录中的完整编号，例如 `GS01-M03`；`M03`、`preparation`、`acceleration`、`follow-through` 不能代替原卡阶段。动作与阶段的技术编号、人物必须一致，阶段窗口必须位于动作窗口内。

当前 `binding` 只接受人工锚点：

```json
{
  "method": "manual_anchor",
  "version": "mit-forward-swing-windows-v1",
  "source": "agent_visual_review_of_decoded_frames_2026-10-22",
  "uncertainty_ms": 150,
  "semantics": "reviewer_estimated_source_stage_not_expert_ground_truth"
}
```

`source` 和 `version` 描述标注来源，必须展示给查看评分的人；它们不能证明阶段或技术标签真实正确。当前没有注册任何自动原卡阶段定位器。外部声称的 `versioned_detector` 会被拒绝。未来自动方法必须有代码注册，并明确声明局部视觉代理语义，不能把三个通用挥拍片段直接命名为十个原卡阶段。

同一动作的同一阶段只能有一个连续窗口。相同人物的不同动作、不同技术或相同阶段发生时间重叠时拒绝；同一动作不同阶段的部分重叠可以存在，但完全相同的区间不能复制成多个阶段。阶段编号增加时，开始与结束时刻均须向后推进，不能倒序或嵌套覆盖；相邻阶段的结束/开始边界可以共享。所有输入键均有白名单，额外的 `passed`、`grade`、`score`、`conditions` 不会被接受。

## 输出与统一服务契约

输出版本为 `gs-card-execution-v1.0.0`，包含 248 个 GS 卡行。每行沿用统一服务的 `indicator_id`、`event_code`、`rule_version`、`rule_kind`、`name_zh`、`runtime_engine`、`run_status`、`reason_codes`、`instance_count`、`instance_status_counts`、`instances`，另提供 `stage_code`。

每个实例保留 `video_id`、`event_id`（锚点编号）、`action_id`、`person_track_id`、`window`、条件实值、阈值、质量统计和原文文档/表格/规则摘要。`source_annotation.binding` 和注释文档 canonical JSON 摘要可追溯人工标注。`stage_windows` 记录请求区间、实际落到的帧区间、原始帧序号与 PTS。

`original_grade` 始终为 `null`，`source_grade_supported` 始终为 `false`。`proxy_grade` 仅为已注册二维条件的完成度。单目不能确认的握拍、负重、力、视线、真实接触等仍保持 `review_required`。缺阶段输出 `not_executed` 和 `gs_stage_annotation_missing`；未标注技术输出 `not_observed`。身份或相机区间不连续等会使整个关联窗口 `unavailable`，不会输出 E 或零分。

正式文件入口的 `input.artifact_files_verified`、`decoder_verified_from_original_video`、`service_eligible` 均为 `true`。摘要算法分别写明：源文件为 `raw_file_sha256`，目录与注释文档为 `canonical_json_sorted_utf8_v1`。总分仍为 `null`；通道汇总使用原文、视觉代理、人工三套独立分母。

`compute_gs_card_execution(...)` 是内部记录重放入口，用于原始观测小样本测试；它不会声称已经验证源文件或解码视频，`service_eligible=false`。`build_gs_research_replay_report(...)` 只用于保留旧研究目录的显式别名，输出也为 `service_eligible=false`，正式服务不调用它。

## 服务接线使用

服务控制的 run 目录可以放置 `gs-stage-annotations.json`，内容必须符合上述注释协议。调用既有 `build_card_execution_report(run_dir)` 时，有文件便从原视频、源帧和标注重新计算 GS，替换统一报告中的 248 个 GS 行并重算汇总；整体仍为 `card-execution-v1.0.0`、298 行，实例保留 `source_annotation`。`input.gs_execution` 记录 GS 验证状态，`gs_stage_windows` 保留阶段窗口证据。

没有该文件时，服务维持原有五个 FS 计算器路径。新增文件、标注内容或其源文件/视频内容变化会使缓存失效，即使大小和修改时间未变也不复用旧 GS 结果。坏标注或 GS 源验证失败只让 GS 不可用，保留已有效计算的 FS；原始 FS 的源身份或输入验证失败仍使整个调用失败，GS 不会遮蔽它。服务不接收外部预计算评分报告。

人工锚点表示带来源和时间不确定度的阶段估计，不代表专家确认，更不代表自动识别了原卡十阶段。接线契约测试会明确模拟生产调用边界来检查隔离、合并和缓存之外的调用规则；这些无媒体测试不计入真实视频验证覆盖。真实三段素材的正式入口回归独立执行。

## 真实回归与边界

三个 MIT OpenCourseWare 片段的 12 个准备期人工锚点产生 60 个实例，涉及 15 张 GS 卡：27 个代理评分、16 个复核、17 个不可用。既有结果的每个条件实值、通过标志、等级、分数与拒绝原因逐项一致。来源为 [MIT PE.710 Tennis 视频课程](https://ocw.mit.edu/courses/pe-710-tennis-spring-2007/pages/video/)，原视频摘要和原始输入摘要在测试夹具内。

`tests/fixtures/gs-card-real-mit-preparation-v1.json` 仅删除源记录中本计算不使用的字段，没有合成或修复坐标；保留全片帧序列、主球员选择和实际解码 PTS。其测试验证观测重放，不冒充读取原视频。正式入口集成测试另从原本地视频及源文件计算；若这些大文件不在运行环境，测试会明确标记 integration skip，核心小夹具回归和反例仍执行。

原研究目录使用短 case id，但其未改动的帧和 summary 使用 `card-<caseid>` 任务编号。正式回归将源文件原样复制到该真实 job id 命名的新目录，注释绑定相同 job id；不改旧帧或视频，不为正式服务开放跨 job 例外。

本模块没有实现自动 GS 十阶段识别，也没有验证原文完整技术等级。对于其它 GS 卡，同一入口可以接收独立可信的阶段锚点；已注册几何规则可计算，人工规则仍需复核，缺锚点保持缺失。
