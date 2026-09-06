# M68 多视频 Pose 可观测性严格超集路由

## 结论

M68 为 M66/M67 的两类实验 Pose 建立了一个不读取动作数值的候选路由：小 ROI 仍按原 M66 合格条件使用；0.30 ROI margin 只有在注册表所需评分关节的有效集合严格包含当前 0.15 ROI 集合时才被采用。它在当前三段真实视频上把 operational measured 从 2,291/2,366 提升到 2,320/2,366，恢复 29、回归 0。

这只是当前数据上的可观测性结果，不是关键点准确率、特征 MAE、事件准确率或独立发布验证。生产路由没有改变。

## 路由合同

当前 registry 的 13 项 required feature 可确定映射到 8 个身体关节：

- left/right shoulder
- left/right hip
- left/right knee
- left/right ankle

对每个 margin 候选帧，路由器计算 baseline 与 candidate 的 required-joint valid set。候选仅在满足以下条件时选中：

1. baseline 的每个有效 required joint 在 candidate 中仍然有效；
2. candidate 至少新增一个有效 required joint；
3. feature-to-joint 映射完整；任何未知 feature 都 fail closed。

选择过程不读取 feature value、事件切分结果、指标状态、grade 或 threshold，因此不能按动作表现挑选更有利的 Pose。路由器版本为 `required-joint-validity-superset-v1.0.0`。

## 三视频结果

- margin 候选帧：410
- 选中 / 拒绝：44 / 366
- 与 M66 小 ROI 合并后的实验帧：193
- feature-vector complete：2,297 → 2,329，恢复 32、回归 0
- operational measured：2,291 → 2,320，恢复 29、回归 0
- measurement hard fail：18，不变
- 非 hard-fail feature incomplete：28

M67 无筛选组合也是净恢复 29 条 operational measurement，但包含恢复 30、回归 1。M68 在当前三视频上保留相同净增益并消除该回归，同时只路由 44 个 margin 帧。

## 产物

- 聚合机器报告：`reports/measurement-recovery-m68/summary/report.json`
- 每视频实验目录：`reports/measurement-recovery-m68/required-joint-superset/<video_id>/`
- 每目录包含 routed frames、固定边界比较、实验报告、H.264 动态对比和视频验证。

动态视频展示完整受影响时间范围，而不是单张截图。左侧为当前 Pose，右侧为实验路由 Pose；叠加只用于解释关节有效性变化，不是真值标注。

## 发布门禁

当前仍缺：

- routed 与 rejected 帧的人工校正关键点；
- 分视角 feature MAE/P95/Bias；
- 在结果揭示前冻结协议的独立视频发布测试。

因此 `production_enabled=false`、`automatic_fallback_enabled=false`，生产继续使用 0.15 margin 与 32px 最小 ROI。13 项保持 F2，A～E、threshold、F3/F4 均未生成或晋级。

## 验证

- 全仓测试：Ran 601，OK，16 skipped optional dependencies
- 机器契约：3 份实验报告和 1 份聚合报告通过 Python validator 与 Draft 2020-12 Schema
- 来源追溯：3 份实验报告与 M67 基线共 4 个 SHA256 binding 全部通过
- 动态视频：44/336/66 帧，H.264，首/中/末均可解码
- 主报告：163 个本地引用，缺失 0；本地 HTTP 主页与视频均返回 200
