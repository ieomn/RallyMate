# M73 三视频 WholeBody133 显式映射补点

## 结论

WholeBody133 在当前残差帧上确实产生了更多有效全身/足部采样点，但这些新增点没有再完成任何评分指标实例。这是受控负结果，不应隐藏：当前剩余瓶颈不是“还缺一个更密骨架模型”，而是事件区间内的连续时序覆盖、视频边界和必要阶段代理。

固定同一候选事件边界、Track、时间戳、特征函数和质量门禁后：

- M72：2,346/2,366 个特征向量完整，2,335/2,366 个门禁后可测；
- M73：仍为 2,346/2,366 与 2,335/2,366；
- WholeBody133 候选输出 254/258 帧；
- 49 帧新增 101 个有效同名关节点；
- 新增完整特征实例 0、新增 operational measured 0；
- 回归和 M72 恢复丢失均为 0。

点更多而指标不增加，直接说明点数量不能替代事件级特征合同，也不能作为关键点准确率或评分能力。

## 模型与跨拓扑合同

候选模型为官方 RTMPose-M COCO-WholeBody 256×192：

- candidate：`rtmpose-m-coco-wholebody133-256x192`；
- checkpoint SHA256：`3DA02694CD6479D3B333FF42EBD0723F96BFA06ADAC1DB1E2E815ED2E9E1B02D`；
- 原生拓扑：17 body + 6 foot + 68 face + 21 left hand + 21 right hand；
- 状态：analysis candidate，不是 F4 scoring model。

融合版本为 `mapped-topology-missing-keypoint-addition-v1.0.0`。安全合同要求：

- baseline 固定为 M72 Halpe26；
- candidate 固定为 WholeBody133；
- 只允许显式同名映射左右肩、髋、膝、踝以及 big toe/small toe/heel；
- 只有 M72 点无效且 WholeBody 对应点有效时才复制坐标；
- 复制后保留 Halpe26 的 index/name/downstream ID 与整体 topology/order；
- M72 已有效点逐字节保持；
- detection、Track、bbox、timestamp、其他人物和所有非 Pose 字段不得变化；
- 决策不读取 feature value、event outcome、grade 或 threshold。

## 三视频结果

- `3ae77...`：93/94 个候选输出，39 个 changed frames，85 个补点，新增完整指标 0；
- `850cb...`：101/103 个候选输出，10 个 changed frames，16 个补点，新增完整指标 0；
- `8d775...`：60/61 个候选输出，0 个 changed frames，0 个补点；阶段代理缺失没有被错误修复。

M72 已恢复集合全部保留。因为不存在新增完整指标，本轮没有把任何 WholeBody 补点写入生产、评分或标定数据集。

## 剩余阻断与后续方向

13 条 non-hard-fail operational residual 保持不变：

- 9 条事件内观测覆盖低于特征合同；
- 2 条视频起点事件被边界截断；
- 2 条 FS09-M05 必需 Pose 阶段代理未被观测。

下一步应审计这些事件的真实 timestamp 网格与短缺失结构，区分“可由严格双侧有界插值恢复的短 gap”和“必须保持 unavailable 的长 gap/单侧/边界外推”；同时人工复核阶段边界。任何插值都必须显式记录 source frames、最大 gap、raw 值与低置信度，不得跨事件、不得外推、不得降低现有门禁。

## 动态证据

- 视频 1：`reports/measurement-recovery-m73/wholebody133-mapped-fusion-v1/3ae77ee3271d67de171585a5c39ddd69/m72-vs-wholebody133-mapped-changed-browser.mp4`，112 帧、3.735924 秒、H.264；
- 视频 2：`reports/measurement-recovery-m73/wholebody133-mapped-fusion-v1/850cb0006b406c7176eeda8d711cd065/m72-vs-wholebody133-mapped-changed-browser.mp4`，130 帧、4.333333 秒、H.264；
- 机器汇总：`reports/measurement-recovery-m73/summary/report.json`。

两段视频都展示实际点级变化，即使没有指标恢复也保留负结果。浏览器实测均为 1920×720、`readyState=4`、媒体错误为空。

## 安全状态

M73 没有修改生产默认、自动 fallback、事件边界、特征门禁、质量门禁、F0～F4、grade 或 threshold。人工事件、人工校正关键点、多教练标定和独立测试仍缺失，因此 13 项继续为 F2，输出继续是 `calibration_required` 或 `unavailable`。

最终验证为全仓 630 tests OK（16 个可选依赖跳过）；3 份逐视频报告和 1 份汇总通过 Python validator、Draft 2020-12 Schema 与所有 source/artifact SHA 重放；主报告已内嵌两段动态对比。
