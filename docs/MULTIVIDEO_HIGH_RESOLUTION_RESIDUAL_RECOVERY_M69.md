# M69 多视频残差高分辨率 Pose 恢复

## 结论

M69 只处理 M68 后仍然非 hard-fail、但 required-feature 向量不完整的残差事件。三段真实视频共有 28 条残差指标实例、13 个事件和 258 个事件帧。RTMPose-M Halpe26 384×288 候选在 0.30 ROI 上成功输出 255 帧，经 required-joint 严格超集门禁后只采用 40 帧。

固定事件边界并保留原质量门禁后，feature-vector complete 从 2,329/2,366 提升到 2,335/2,366，operational measured 从 2,320/2,366 提升到 2,326/2,366；两种口径均恢复 6、回归 0。非 hard-fail 残差由 28 降为 22，18 条 measurement hard fail 不变。

这不是关键点准确率、特征 MAE、事件准确率或独立发布验证；生产路由没有改变。

## 实验边界

候选同时改变了两个因素：

- RTMPose-M 输入与权重从 Halpe26 256×192 切到 Halpe26 384×288；
- ROI margin 使用 0.30，最小 ROI 为 8px。

因此当前增益不能单独归因于“分辨率更高”或“裁剪范围更大”。候选只在残差事件帧运行，不改检测、Track、主球员时间线、事件边界、特征公式或质量策略。

## 安全路由

每帧继续使用 M68 的 `required-joint-validity-superset-v1.0.0`：候选必须保留 baseline 的全部有效评分关节，并至少新增一个有效评分关节。选择只读取肩、髋、膝、踝的有效集合，不读取 feature value、事件结果、grade 或 threshold。

255 个有 Pose 输出的目标帧并不会自动被采用；最终 40 个选中、218 个拒绝，另有 3 帧没有候选 Pose。固定边界比较必须逐条报告 recovered 与 regressed，不能只显示净增益。

## 产物

- 残差机器审计：`reports/measurement-recovery-m69/residual-audit/report.json`
- 三视频机器汇总：`reports/measurement-recovery-m69/summary/report.json`
- 每视频实验目录：`reports/measurement-recovery-m69/high-resolution-384-margin030/<video_id>/`
- 每目录包含候选/路由 frames、固定边界比较、实验报告、H.264 动态视频和视频验证。

动态视频只渲染实际恢复指标所在的完整事件窗口。左侧是 M68 当前 Pose，右侧是 384×288/0.30 ROI 实验路由 Pose；两侧都不是人工关键点真值。

## 发布门禁

当前仍缺：

- selected 与 rejected 帧的人工校正关键点；
- 分视角 feature MAE/P95/Bias；
- 分离输入分辨率与 ROI 上下文影响的消融；
- 在结果揭示前冻结协议的独立视频发布测试。

因此 `production_enabled=false`、`automatic_profile_fallback_enabled=false`。生产继续使用当前默认模型与裁剪配置；13 项保持 F2，A～E、threshold、F3/F4 均未生成或晋级。

## 验证

- 全仓测试：Ran 607，OK，16 skipped optional dependencies；
- 3 份逐视频实验报告、残差审计和聚合报告均通过 Python validator、source replay 与 Draft 2020-12 Schema；
- 3 段 H.264 动态对比为 43/58/33 帧，首/中/末可解码并绑定 experiment report SHA；
- 主报告 175 个本地引用、94 个唯一目标，缺失 0；浏览器实测两段内嵌视频 `readyState=4`、无媒体错误，主页与三段视频 HTTP 均为 200；
- `compileall` 与 `git diff --check` 通过。
