# M10 恢复阶段读取与历史规则清退

本批使用 `gs-m10-observability-v1.0.0`。前端只对 GS01-M10-02/03、GS02-M10-02/03、GS03-M10-03/04 六张身体卡读取新应用成绩；另外七张涉及手拍控制或完整准备状态的卡仍无分。所有 13 张旧 M10 卡的成绩都清退，标量与完整条件证据分别保存在 `superseded_conditions` 和 `superseded_condition_evidence`。原文等级始终为空。M09 两张已退役手拍卡沿用原有行为。

## 独立复算

`gs-recovery-evidence.ts` 不从保存的 passed、score、geometry_gate 或 revision 字段授权评分。读取器核对精确版本和来源后，从逐原帧 `reference_points` 重新计算：

- 全 torso 的逐帧中心及尺度，与归一化 `points` 闭合；测量固定尺度为整个所需关节共同有效窗口的肩髋中心距离中位数。
- 原始 PTS、时基与真实时间间隔，完整连续原帧、选中 raw track、primary epoch、derived epoch、相机 epoch；模型的原 detector epoch 与下游 primary epoch 分开验证。
- 至少 7 帧且至少 80 ms 的完整阶段。起末端各取 20% 且至少 3 帧，不延窗或补点。
- 身体旋转卡使用独立动作前准备参照的肩髋投影轴。参照至少 3 帧、80 ms，轴范围不超过 8°；参照必须早于原动作开始。参照首帧到恢复末帧的每一来源绑定和 sample 必须完整连续。中间桥接帧只核来源，不要求它们的关节点足够计算阶段指标。
- 相对参照误差减少、末端最大误差、末端轴范围；稳定性为末端髋速度、髋投影位于双踝区间的比例、末端躯干倾角范围。轴为补偿参考平面中的 180° 周期轴，不是三维旋转；参考坐标不会二次应用相机变换。

阈值从 Python 核心的字面常量生成，并与逐卡目录一起核对。它们是未校准的应用条件，不是原 Word 专家等级。新数值成绩仍由正式应用政策的条件完成度映射 A–E。保存的等级、数值与条件必须和独立复算一致，否则清空成绩。

## 来源、展示与备份

阶段和参照窗口、完整 source geometry、原始双 epoch 绑定、父 action/window 注释、辅助观测、原条件证据均保留。合法 3–5 帧短窗显示未满足计算要求，不称损坏，不赋 E/0，也不提示人工复核。辅助腕距不证明手与球拍控制，七张手拍/完整准备卡不能由辅助 flag 授权。

来源副本不一致时取消分数与有效测量摘要。完整原始记录仍可展开，明确标为“未经验证的输入记录”。备份保存全部来源；再读必须再次验证，保存的 validation 不作授权。用户可以继续选择缺测实例。普通卡与既有 M09 路径不因本批改变。

这里只验证序列化的观测、绑定和算术合同。前端不能独立认证原始媒体、签名或视觉真实身份；全链自洽改写需要外部原始文件核验，不能用保存的 SHA 字符串代替。

## 测试边界

`scoring-demo-web/tests/fixtures/gs-recovery-contract-v1.json.gz` 是后端 `compute_gs_card_execution` 从纯数学 raw records 生成的独立 GS 服务报告的无损压缩。每组保留 producer、scope、源码摘要和预期结果。测试仅为调用统一读取器而投影顶层 version，明确不是生产 unified API 或实际媒体回放。合成正例不计入真实覆盖。

9 组生产服务形状的数学报告覆盖全部 13 个卡 ID。测试覆盖旋转回归、静止但未回归（D40）、匀速平移（稳定性 C60）、缺参照、短窗、模型 raw epoch 0 / primary 2、非零 PTS；并核验缺关节、低置信、原几何或尺度篡改、桥接 sample 缺失、原绑定/时钟/模型 namespace、历史清退和完整备份往返。旧 298 行真实历史报告维持原有非 M10 行与得分。

运行：`python scripts/build_gs_card_display_contract.py --check`；在 `scoring-demo-web` 执行 `node --test tests/gs-recovery-evidence.test.mjs tests/hand-racket-revision.test.mjs tests/card-execution.test.mjs`，以及 typecheck、定向 lint、production build。

本批不宣称真实完整 M10 正例。当前四个 MIT 双反短候选的末端属于恢复中第一脚，不能作为完整 M10 标注；不扩窗取分、不新增真实有分覆盖。
