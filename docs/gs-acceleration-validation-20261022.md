# GS 前挥阶段人工源窗口验证（2026-10-22）

本次在三个既有 MIT OpenCourseWare 视频中新增 11 个 M07 人工候选窗口，产生 55 个逐卡实例，涉及 15 个卡 ID：4 个视觉代理、18 个需复核、33 个不可用。4 个代理全部属于 `GS01-M07-04` 的髋投影与躯干倾角条件；**11 个持拍腕加速实例均不可用，没有新增可评分的腕加速实例**。原文等级数量为 0，总分为 `null`。

这是对可辨阶段边界和严格采样门槛的验证，不是完整十阶段识别，也不是专家技术评级。没有修改生产代码、原视频、原始推理结果、目录阈值或既有 GS 夹具。

## 原卡语义和标注范围

来源为 [MIT PE.710 Tennis, Spring 2007 视频课程](https://ocw.mit.edu/courses/pe-710-tennis-spring-2007/pages/video/) 的正手、单手反手和双手反手示范。沿用原文件及已完成的推理；432×240 画面、约 15 帧/秒，时间计算使用实际解码 PTS，不用标称帧率推算。

逐帧检查了原有 12 次动作附近的全部 195 个上下文帧，再冻结窗口，之后才读取新的逐卡输出。没有依照得分改边界或筛除失败窗。单反原先属于保留验证素材，本次新增标签是后续非专家阶段复核，不冒充新的独立技术真值集。

- `GS01-M07 手臂加速与挥拍`：原文起点是持拍手开始主动加速，终点是球拍轨迹与预测击球轨迹开始收敛。这里只能估计手臂前挥与球拍、球在二维画面中的接近，**没有恢复预测球路**，也不能证明真实接触、三维释放或主动受力。
- `GS02-M07 双手加速与挥拍`：原文开始、结束字段为空。沿用已经提交的 `stage-boundaries-20261022-GS02-M07` 工程边界，估计双腕共同前挥至球拍、球进入身前接近候选区。
- `GS03-M07 单手加速挥拍`：原文开始、结束字段为空。沿用已经提交的 `stage-boundaries-20261022-GS03-M07` 工程边界，估计非持拍手分离后的单手前挥至身前接近候选区。

三种标注均使用 `manual_anchor`，语义固定为 `reviewer_estimated_source_stage_not_expert_ground_truth`。正手边界不确定度为 ±150 ms，反手为 ±100 ms；每个端点另有明确的原帧夹逼区间。这些不确定度没有被用来扩大计算窗口。起点中的“加速”是原卡阶段名称及视觉估计，不能替代数值加速度测量。

没有为 M04 蹬地、M05 转髋或 M06 转胸强补窗口，也没有把三个通用挥拍片段均分、复制成十阶段。

## 固定窗口与失败证据

以下均为视频从零开始的原始帧序号，包含首尾帧；每一帧的实际 PTS、时间基、视频 SHA256 和端点截图 SHA256 均在新窗口夹具中。

- 正手 4 窗：35–39、98–102、157–161、219–223。每窗 5 帧，约 266–267 ms。20 实例为 4 代理、12 复核、4 不可用。
- 单反 3 窗：35–36、91–92、151–152。每窗只有 2 帧，66–67 ms，15 实例全部因 `stage_source_observations_missing` 不可用。第 4 拍仍保留在复核清单中：第 206 帧球接近画面右侧，第 207 帧球拍到画面边缘且球不可分辨，M07 终点未解决，未送入阶段执行器。
- 双反 4 窗：26–28、82–84、140–141、194–196。分别为 3、3、2、3 帧，67–133 ms。20 实例为 6 复核、14 不可用。

55 实例的顶层失败或复核原因包括：20 次源阶段不足 3 帧、18 次人体姿态不能恢复专项观测、13 次必需条件证据缺失、3 次联合关键点有效比例低于 70%。原因可重叠，不能把原因次数相加当实例总数。

腕速度增加需要独立、连续且足够长的起止端点窗口。本次正手 5 帧仍不足，保留 `continuous_endpoint_window_missing`；双反三个 3 帧窗的双腕等必需点只有 2/3 同帧有效，另外未过 70% 联合门槛。窗口未扩展到准备或随挥阶段，也没有将未知转为 E 或 0。

4 个正手平衡代理仅报告五帧中的二维髋中心落在双踝横向区间、躯干倾角范围。其工程条件完成度为 A/100；这不确认真实足底支撑、受力或原文整卡质量。所有报告仍保留 `visibility_verified=false` 和 `image_bounds_verified=false`，没有把数值几何有效性升级为可见性真值。

## 数据与可复算性

新增文件：

- `tests/fixtures/gs-acceleration-windows-20261022.json`：全部 12 次人工检查、11 个可执行候选、1 个未解决窗口，含原卡边界来源、视觉描述、误差区间和视频/源帧/截图摘要。
- `tests/fixtures/gs-acceleration-results-20261022.json`：正式文件入口重算的全部 55 实例、条件实值、门槛、空值原因、原帧分母，以及实际运行的 79 个导入模块/规则数据文件摘要。
- `scripts/replay_gs_acceleration_validation.py`：只从原视频、源帧、主球员选择及固定人工标注调用正式 `build_gs_card_execution_report`。运行前后核验导入源码与指定 Git 提交一致；不信任保存的评分。
- `tests/test_gs_acceleration_validation.py`：复用已经提交的完整 MIT 原始观测小夹具，重新计算 55 实例，并验证缺帧、边界时钟、不可用与评分的区分。

原始数据及截图留在本地 ignored 目录。生产回放通过 `run_dir`、`summary.job_id`、逐帧 job ID、源文件 SHA256 和原视频 SHA256 检查，并重新调用 FFmpeg 实际解码 PTS。没有改动旧研究目录；复用先前已按真实 job ID 原样复制的正式验证目录。

运行源码来自干净 `git archive cb99462`，仅覆盖本次新增脚本与夹具，不覆盖任何 `src` 文件。结果保存每个实际导入源码的原字节 SHA256、UTF-8 去 BOM 且 CRLF/CR 转 LF 后 SHA256，以及该提交中对应 blob 的原字节 SHA256；目录数据也在此清单。标注摘要为冻结文件的原字节 SHA256，目录与注释文档的内部摘要算法另有明确字段。

冻结窗口 SHA256：`db01b85f27e6ea1dce71c0737b45de1d5ffca8a5b015b632d8aaffcd1b2b3eb7`。

本机完整报告、截图与验证日志位于 `.codex_tmp/gs-acceleration-validation-20261022/`。原始源视频和这些 ignored 文件不进入提交。没有这些媒体时，提交内的原观测回归仍可执行，但不能声称刚刚解码验证了原视频。

在干净检出中运行：

```powershell
$env:PYTHONPATH='src;.'
python scripts/replay_gs_acceleration_validation.py --manifest tests/fixtures/gs-acceleration-windows-20261022.json --artifact-root 'C:/Users/Admin/Documents/网球' --output-directory '.codex_tmp/gs-acceleration-replay'
python -m unittest tests.test_gs_acceleration_validation tests.test_gs_card_execution tests.test_card_real_window tests.test_card_evaluation tests.test_card_rule_catalog -v
```

干净检出专项与相关回归共运行 59 项，其中 58 通过、1 项因干净目录未放媒体而明确跳过。另在同一干净源码下显式选择现有原媒体，原 GS 三视频正式入口集成测试通过，保留旧准备阶段的 27/16/17 结果。新增三视频正式回放则全部实际完成，产生本文的 4/18/33 结果。
