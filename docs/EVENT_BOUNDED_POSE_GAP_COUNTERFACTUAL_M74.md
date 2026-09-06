# M74 事件内有界 Pose 缺口反事实

M74 回答的是一个窄问题：M73 剩余 13 个不可用指标实例中，有多少只是被事件内部的短时 Pose 缺口卡住。它不回答插值点是否准确，也不改变生产评分。

合同如下：

- 只处理同一事件内的缺失运行；
- 左右两侧都必须存在 confidence≥0.25 的真实 Pose 观测；
- 两个边界观测的 `timestamp_ms` 跨度最多 160 ms；
- 坐标按真实时间线性插值，有效置信度取两端较小值；
- 不允许首尾外推、跨事件借帧、补 0、生成事件阶段或修改事件边界；
- 所有输出都是 `counterfactual_only`，不能进入生产评分。

真实三视频审计得到 142 个去重后的 event×joint×frame 候选插值点。它们来自 157 个可接受的内部缺口运行，边界跨度为 66～134 ms。按指标分别重算时有 226 次引用，产生 20 个 invalid→valid 特征转换，使 7/13 个残余特征向量反事实完整；另外 6/13 仍不可用。

这 7 个包括 FS01-M03/M04 与 FS02-M03/M04/M05 的部分事件实例。两个 FS09-M05 仍缺 `hip_deceleration_to_double_support_proxy_ms` 所需阶段代理，插值不会也不得制造该阶段。视频起点事件即使数值反事实变完整，仍因边界截断缺少独立真实性证据，生产状态不变。

机器报告：`reports/measurement-recovery-m74/event-bounded-gap-audit-v1/report.json`。其中每个候选点都包含插值坐标、左右证据帧/时间/置信度；每个指标实例都包含前后 feature value、valid fraction、reason 和 source frames。生产恢复计数固定为 0，无 A～E、无阈值、无成熟度晋级。

下一步是生成不显示插值预测的盲化人工关键点工作包，独立采集这些 frame×joint 的坐标/不可见原因，再计算插值 MAE、P95、Bias、有效率和分视角误差。没有这些真值前，不应把反事实 Pose 序列化成 Worker 可加载的生产 frames。

验证结果：全仓 636 tests OK（16 skipped）；M74 专项 6/6；报告通过 source SHA replay 和 Draft 2020-12 Schema；主报告本地链接缺失为 0，浏览器现有 23 段动态视频均可解码播放。
