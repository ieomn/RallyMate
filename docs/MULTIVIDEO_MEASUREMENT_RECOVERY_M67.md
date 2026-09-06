# M67 三视频 Pose 可观测性恢复实验

本实验回答一个窄问题：在不换 RTMPose 模型、不改事件边界和质量门禁的前提下，扩大主球员 ROI 上下文能否减少 13 项指标的 Pose 观测缺口。

生产基线使用 RTMPose-M Halpe26 256×192、ROI margin 0.15、最小 ROI 32px。M67 只在已有 baseline Pose、但候选事件存在特征观测缺口的帧上将 margin 改为 0.30。三段真实视频共重推理 410 帧，固定原事件边界重算后：

- 特征向量恢复 14 条、回归 1 条；
- 保留原事件/身份门禁后，operational measurement 恢复 11 条、回归 1 条；
- 有效关键点数增加 76 帧、减少 107 帧、不变 227 帧。

因此“更大的 ROI 一定提供更多有效点”不成立。只保留 baseline ROI 被画面边缘裁切的 233 帧也未隔离回归，不能据此启用自动 fallback。

M66 小 ROI 与 M67 margin 的目标帧互不重叠。组合结果通过合并完整帧并实际重算特征得到：2,331/2,366 条特征向量完整，2,320/2,366 条在原 measurement gate 后 measured；相对生产基线实际恢复 30、回归 1，净增加 29。18 条 hard fail 不变，仍有 28 条非 hard-fail 特征缺口。

机器汇总为 `reports/measurement-recovery-m67/summary/report.json`。动态视频位于：

- `reports/measurement-recovery-m67/roi-margin-0.30/3ae77ee3271d67de171585a5c39ddd69/current15-vs-experimental30-changed-browser.mp4`
- `reports/measurement-recovery-m67/roi-margin-0.30/850cb0006b406c7176eeda8d711cd065/current15-vs-experimental30-changed-browser.mp4`
- `reports/measurement-recovery-m67/roi-margin-0.30/8d7754d0de6d315674013d5b69a0b6ba/current15-vs-experimental30-changed-browser.mp4`

这些结果只说明可观测性，不是人工关键点 MAE/P95、事件准确率或动作等级。生产保持 margin 0.15、最小 ROI 32px、无自动 fallback；人工关键点真值、分视角误差和预注册独立视频发布测试完成前，不修改生产路由，不生成阈值，不推进 F3/F4。
