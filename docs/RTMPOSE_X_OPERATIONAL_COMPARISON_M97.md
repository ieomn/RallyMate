# M97 RTMPose-X 384 operational 同范围增益验证

> 状态：`experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery`  
> 日期：2026-09-05  
> 用途：在 M68/M70/M71 已冻结的三视频开发范围内，检验 X384 是否保留既有恢复集合；本报告不是准确率评测或晋级记录。

## 1. 结论

M97 复用了 M71 的 3 段开发视频、258 个残差帧，以及逐帧相同的 source frame、processed frame、
timestamp、track、ROI margin、最小 ROI 尺寸、`analysis` profile 和 flip-test。X384 仍通过 M70 锚定的
required-joint validity 严格 superset/no-regression 路由进入固定边界特征重算；事件边界、特征函数和原始
质量门禁均未改变。

X384 在 258 个目标帧中产生 254 个 Pose，严格路由选中 56 帧。重算 2,366 个指标实例后：

| 基线/投影 | feature complete | operational measured | 相对 M68 feature 恢复/回归 | 相对 M68 operational 恢复/回归 |
|---|---:|---:|---:|---:|
| M68 | 2,329 | 2,320 | 0 / 0 | 0 / 0 |
| M70 | 2,338 | 2,327 | 9 / 0 | 7 / 0 |
| M71 | 2,344 | 2,333 | 15 / 0 | 13 / 0 |
| M97 X384 | 2,342 | 2,331 | 13 / 0 | 11 / 0 |

相对 M70，X384 的 feature 和 operational 恢复集合都新增 4 项，丢失 0 项；相对 M71，两类集合都
新增 1 个不同恢复项，但丢失 3 个 M71 恢复项，净计数各少 2。于是 X384 保留了 M70 的恢复集合，
却没有保留 M71 的恢复集合，不能替代 M71 的本开发集结论。

这些数字只表示冻结事件边界、特征函数和质量门禁下的可计算性/可测量性变化。没有人工关键点真值，
因此不能解释为 Pose、事件或评分准确率提升，也不能用于模型晋级。

## 2. 冻结输入与可比条件

协议 `models/rtmpose/m97-x-operational-protocol.json` 的版本为
`rtmpose-x-operational-extension-2026-09-04.1`，SHA-256 为
`FEA766D88E0D2072B2A2859E4FF144FD8CE9DDE4CD9FA154493BC1DB938C42F1`。它绑定：

- M68 fixed-boundary 基线汇总，SHA-256
  `BBE5991F09381B35A2AD3097EE5BF9BDCDFDC89B16966F064C1F7CBBB850229D`；
- M70 锚定路由基线汇总，SHA-256
  `A1A35DC8709E14E6CE03F20CAA100F733C31908B8B0FB26CECD83C4A5B55D554`；
- M71 L384 对照汇总，SHA-256
  `2F0D19D6CEFB627B1FA821CCBEEE64C5E7C66A0BACE090DF76F5B1C530DA9A8F`；
- X384 checkpoint，SHA-256
  `7FB6E239601082A06CA8442FEB7A9774B8D77A1C37911B2AD0F27C1E84BE4D21`；
- X384 config，SHA-256
  `961E5704E4983F27173BC008C17F08E8C2C5907FDB104EA8669AD06C4C68B678`；
- 三段开发视频、逐帧 M68/M70/M71 输入和报告，以及当前指标 registry。

完整的 62 项输入/结果产物路径、字节数、角色和 SHA-256 位于
`reports/measurement-recovery-m97/field-change-record.json`；该记录本身 SHA-256 为
`87647B58D804CBF280C7C293CF20F3A5EA1126EC29AB9CBF6C89A63DE802C2BA`。当前生产部署注册表仍为
`models/rtmpose/deployment-presets.json`，SHA-256
`A95B7B5F225C025371157C22A9A60397874DE03C85B4EAC512B3B9C443F14C69`，默认 preset 仍是
`rtmpose-m-halpe26-online`。

密封 `c235…` holdout 没有被打开或重新计算哈希，也不在 M97 artifact manifest 中。三段开发视频是已被
反复用于选择和诊断的开发输入，不是独立发布验证集。

## 3. 逐视频推理与路由

| video ID | 目标帧 | Pose 返回 | 严格路由选中 | 相对 M68 feature 恢复/回归 | 相对 M68 operational 恢复/回归 |
|---|---:|---:|---:|---:|---:|
| `3ae77ee3271d67de171585a5c39ddd69` | 94 | 93 | 26 | 8 / 0 | 6 / 0 |
| `850cb0006b406c7176eeda8d711cd065` | 103 | 101 | 30 | 4 / 0 | 4 / 0 |
| `8d7754d0de6d315674013d5b69a0b6ba` | 61 | 60 | 0 | 1 / 0 | 1 / 0 |
| 合计 | 258 | 254 | 56 | 13 / 0 | 11 / 0 |

三帧没有唯一可用的 source detection，因此没有进入计时 Pose 调用；另有一次小 ROI 的
`PoseEstimator` 调用很快返回空 Pose。这解释了目标帧 258、计时调用 255、Pose 返回 254 三个分母的差异。

## 4. 相对 M70/M71 的精确集合差异

相对 M70，以下 4 个实例同时是额外 feature 与 operational 恢复；M70 已恢复项丢失数为 0：

- `3ae77ee3271d67de171585a5c39ddd69 / fs01-026-9eb9dc227294 / FS01-M03`；
- `3ae77ee3271d67de171585a5c39ddd69 / fs01-026-9eb9dc227294 / FS01-M04`；
- `3ae77ee3271d67de171585a5c39ddd69 / fs02-025-d71099ebf537 / FS02-M03`；
- `850cb0006b406c7176eeda8d711cd065 / fs01-005-d9d36428b672 / FS01-M04`。

相对 M71，X384 额外恢复 1 项：

- `850cb0006b406c7176eeda8d711cd065 / fs01-005-d9d36428b672 / FS01-M04`。

但 X384 同时丢失以下 3 个 M71 已恢复实例；feature 与 operational 的集合差异相同：

- `850cb0006b406c7176eeda8d711cd065 / fs01-024-3bcd2a8b290f / FS01-M03`；
- `850cb0006b406c7176eeda8d711cd065 / fs01-030-ef3407c9db93 / FS01-M03`；
- `850cb0006b406c7176eeda8d711cd065 / fs01-030-ef3407c9db93 / FS01-M04`。

“相对 M70/M71”在这里是把两者各自相对同一 M68 基线的恢复集合做集合差，不是把不同边界、不同指标
或不同门禁的总数相减。

## 5. 延迟口径

M97 的 255 次 GPU 同步 `PoseEstimator` 调用只测 X384 的 `analysis` + flip-test 单 ROI 调用，不含视频
解码、来源绑定、路由和 fixed-boundary evaluation：均值 24.835216 ms、P50 21.1203 ms、P95
25.57492 ms，范围 0.024～300.1518 ms。三次独立进程的首次调用约为 297～300 ms 冷启动离群值；
0.024 ms 是上述空 Pose 的快速返回。去掉每个进程首次调用后为 252 次，P50 20.98575 ms、P95
25.423515 ms；该集合仍含快速空返回。

M96 的 45 帧 M/L/X 同帧横向诊断使用统一 `realtime`/no-flip：M、L、X 的单 ROI P50 分别是
9.1244、10.6427、11.7265 ms。M97 与 M96 的 profile、flip 和样本范围不同，不能计算或陈述跨 scope
速度倍率；M97 同一轮也没有重跑 M/L，因此没有直接的 M/L 延迟 comparator。

## 6. 证据定位与未完成项

- 汇总报告：`reports/measurement-recovery-m97/summary/report.json`，SHA-256
  `46A3D690C73C827BE1B143A75F9192A513ED37B66268FBF808FD896C2DA2160E`；
- 人类可读冻结摘要：`reports/measurement-recovery-m97/summary/summary.md`，SHA-256
  `7C019C7DCD4BCDD6749B89C9D108F81FD5698AC97FA3711541795A141C0F71F2`；
- 三份逐视频报告 SHA-256 依次为
  `EAEFF102DD2A70579AA02F3BAB217C09F4034BFD16FDA30F69661DC107F7DD09`、
  `7C06A3A7AED922C615CEDF4635161FD9190EAC3A387607B4C5039FD24B7BFFA8`、
  `6AABCDFB10446CDB2ADB2EF9A4C64C58A5F08D2902AB21A9A594D053BDD55B6D`；
- 聚焦验证为 20/20 通过，相关 Python 编译通过；这只证明实现和冻结合同一致。

尚未完成：人工 Halpe26 关键点准确率比较、逐视角特征误差、预注册的独立视频发布验证、候选晋级、
生产默认模型变更。M97 不生成 grade 或 threshold，不修改事件/特征/测量门禁，不启用自动 profile fallback，
也不声称准确率或评分质量提升。
