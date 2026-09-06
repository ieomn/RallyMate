# M66 三视频 Pose 测量恢复

本里程碑回答一个很窄的问题：当前 RTMPose-M Halpe26 在远场小检测框上没有 Pose 输出时，只降低 ROI 最小尺寸，能恢复多少 Pose 特征向量，以及其中多少能在不改变事件/身份质量门禁的前提下成为有效测量。

当前生产结果没有变化：三视频 2,366 条指标实例中，2,297 条 required-feature 向量完整、69 条不完整；叠加原 measurement gate 后为 2,291 measured / 75 unavailable。18 条受 measurement hard fail 影响，其中 6 条向量完整、12 条向量也不完整。

预检只选择已有主球员检测、仍在原 `max_players=2` 调度内、且明确被 32px ROI size guard 跳过的帧。三个视频合格目标分别为 0、131、18。实验保持视频、检测、主球员时间线、候选事件边界、RTMPose 权重、特征函数和质量策略不变，仅把最小 ROI 改为 8px。

149/149 个目标帧产生 Pose。固定边界重算恢复 22 条特征向量、回归 0 条；保留原 measurement gate 后，20 条指标实例从 unavailable 变为 measured，回归仍为 0。实验投影为 2,311 measured / 55 unavailable；18 条 hard fail 不变，另有 37 条非 hard-fail 特征缺口。

这只是可观测性实验。149/149 不代表关键点准确，2,311/2,366 不代表评分准确，也不是生产结果。生产仍使用 32px，自动 fallback 关闭；人工关键点真值、分视角误差和独立发布审核完成前，不会把 8px 接入正式推理或标定。

入口：

- [三视频汇总 HTML](../reports/measurement-recovery-m66/multivideo-small-roi-v1/index.html)
- [机器可读汇总](../reports/measurement-recovery-m66/multivideo-small-roi-v1/report.json)
- [视频 2：32px 与 8px 动态 A/B](../reports/measurement-recovery-m66/small-roi-v1.1/850cb0006b406c7176eeda8d711cd065/current32px-vs-experimental8px-browser.mp4)
- [视频 3：32px 与 8px 动态 A/B](../reports/measurement-recovery-m66/small-roi-v1.1/8d7754d0de6d315674013d5b69a0b6ba/current32px-vs-experimental8px-browser.mp4)

验证状态：全仓 588 tests OK（16 个可选依赖跳过）。6 份 M66 JSON 已通过 Draft 2020-12 Schema；两段视频为 H.264，并通过首/中/末帧解码验证。grade=0、threshold=0、F3/F4 promotion=0。
