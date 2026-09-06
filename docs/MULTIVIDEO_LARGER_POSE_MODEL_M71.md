# M71 三视频 RTMPose-L 受控扩展

## 结论

RTMPose-L Halpe26 384×288 在当前三段视频的少量残差帧上，确实比 M70 的 RTMPose-M 路由补出了更多评分必需关节；但增益集中在两段视频，第三段没有任何候选帧通过严格超集条件。因此本轮没有把 RTMPose-L 全局替换为生产模型，也没有把“点更多/有效点更多”写成准确率或评分效果。

固定同一候选事件边界、Track、时间戳、特征函数和质量门禁后，2,366 个指标×事件实例的结果为：

- M68：2,329 个特征向量完整，2,320 个门禁后可测；
- M70：2,338 个特征向量完整，2,327 个门禁后可测；
- M71：2,344 个特征向量完整，2,333 个门禁后可测；
- M71 相对 M70 新增 6 个完整特征实例和 6 个门禁后可测实例，丢失 M70 恢复为 0；
- 18 个 measurement hard fail 不变，非 hard-fail operational 残差从 M70 的 21 降为 15。

这些数字只表示当前固定边界上的 Pose 可观测性，不是人工关键点 MAE/P95/PCK、Event F1、等级准确率或生产发布结论。

## 候选模型与实验边界

候选来自版本化 `models/rtmpose/model-candidates.json`：

- candidate：`rtmpose-l-halpe26-384x288`；
- checkpoint：`rtmpose-l_halpe26_384x288.pth`；
- SHA256：`734182CE2409BA84E96EA2A6361AEDB0EB40722F7AB52BB69331C73477D6A650`；
- 原生拓扑：Halpe26；
- 输入：384×288；
- 模型注册表：`rtmpose-halpe26-candidates-2026-08-22.2`。

实验只处理 M70 已冻结的 258 个残差帧，并逐帧复用 M68 的真实裁剪上下文：0.15/32、0.30/32 或 0.15/8。258 帧中 254 帧产生 L Pose。路由从 M70 输出出发，只有当 L 的注册表必需关节有效集合是 M70 的严格超集、且不丢失任何 M70 有效必需关节时才替换。选择过程不读取特征值、事件结果、grade 或 threshold。

## 分视频结果

- `3ae77...`：94 个目标帧、93 个 L Pose 输出、27 帧被选中；相对 M70 新增 3 个完整特征实例、3 个 operational measured 实例。
- `850cb...`：103 个目标帧、101 个 L Pose 输出、19 帧被选中；相对 M70新增 3 个完整特征实例、3 个 operational measured 实例。
- `8d775...`：61 个目标帧、60 个 L Pose 输出、0 帧通过严格超集；没有新增恢复，因此不生成伪变化视频。

## 动态证据

- `reports/measurement-recovery-m71/rtmpose-l-384-context-extension-v1/3ae77ee3271d67de171585a5c39ddd69/m70-vs-rtmpose-l-extension-changed-browser.mp4`：63 帧、H.264、首/中/末解码通过；
- `reports/measurement-recovery-m71/rtmpose-l-384-context-extension-v1/850cb0006b406c7176eeda8d711cd065/m70-vs-rtmpose-l-extension-changed-browser.mp4`：88 帧、H.264、首/中/末解码通过；
- 机器汇总：`reports/measurement-recovery-m71/summary/report.json`。

视频左侧是 M70 锚定 RTMPose-M 384×288，右侧是 M70 加 RTMPose-L 的严格扩展。画面只展示实际新增恢复涉及的完整事件窗口，不是单张截图。播放器和机器验证均绑定视频、frames、实验报告与 ffmpeg SHA。

## 安全结论与下一步

M71 没有修改生产默认、自动 fallback、事件边界、特征门禁、质量门禁、F0～F4、grade 或 threshold。13 项仍为 F2；人工事件、人工校正关键点、逐视角特征误差、教练标定和预注册独立视频发布测试仍缺失，所以结果必须继续是 `calibration_required` 或 `unavailable`。

若要判断 RTMPose-L 是否应全局替换 RTMPose-M，下一步不是继续看有效点数量，而是使用现有盲标工作台，对 M70/M71 发生差异的帧做双人独立关键点标注和裁决，按视角计算必需关节 MAE/P95/PCK，再在未参与选择的独立视频上执行预注册发布验证。

最终验证为全仓 618 tests OK（16 个可选依赖跳过）；三份 per-video 报告与聚合报告通过 Python validator、Draft 2020-12 Schema 和 source/artifact SHA 重放；两段视频通过 H.264 首/中/末解码及浏览器实际加载。
