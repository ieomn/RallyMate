# 时序逐卡的同帧人体重复几何门

本变更将 `pose-person-geometric-association-v1.0.0` 的原始记录审计接到时序卡消费者。它识别的是同一原帧中不同 raw track 的重复几何，不是视觉身份真值、确认换人或步法类别判定；稳定的选人编号、相机段、PTS 也不保证姿态模型一定选对了人。

## 来源账本

`pose_sequence_from_records` 在选择单个人物、相机变换、关节归一化之前，使用该原帧全部姿态和 `class_name == "player"` 的检测框重新运行审计。它不接受输入记录中保存的清除标志，不修改原记录，不删除冲突点或原帧。缺少原图尺寸时保留未审计原因，不声称冲突不存在。

`PoseSequence.coordinate_metadata.source_pose_person_association` 保存版本 `source-pose-person-association-ledger-v1.0.0`、helper 政策版本及按原帧编号索引的完整结果。每行包含原帧、处理帧、视频 SHA、PTS、有理时间基、所选 raw track，以及 helper 的距离、框归属和拒绝列表。主要使用者身份与模型自身 epoch 都不能替代 raw track。

消费时逐帧核对账本与已有 decoder/model 原来源绑定。错误版本、缺行、错帧、错时钟或错 raw track 产生 `source_pose_association_evidence_invalid`；未审计行产生 `source_pose_association_audit_unavailable`。原有时钟、几何和连续性失败保持原诊断优先级。

## 严格限制到实际消费区域

`temporal-pose-person-association-gate-v1.0.0` 的硬失败码统一为 `selected_pose_person_association_conflict`，原 helper 内部原因 `pose_duplicate_body_different_tracks` 原样保留。

- 五张时序卡的量测区间检查全部原帧，不把冲突帧变成缺点后继续尝试 70% 覆盖率。
- 正常预压定位检查其已选局部下降/回升确认区间及前一原帧转折见证。整个事件或搜索区间中的其他冲突不自动污染该结果。
- 局部下降兜底检查前一原帧、下降段和完整回升确认段。被拒绝候选保留 `rejected_candidates`、精确消费帧和审计证据；有效候选仍沿既有“最早完整局部下降”规则选择，不按分数选择。确认帧不扩大量测分母。
- 需要后继事件的两卡检查锚点到后继开始之间完整间隔。该间隔的失败只阻止依赖它的卡，不影响没有消费该区间的制动卡。
- FS03/FS04 周期消费者复用 `_window`，因此按其完整人工候选窗获得相同硬门；没有改动周期算法或阈值。

`evidence.pose_person_association`、预压 `phase_pose_person_association` / `fallback` 和后继 `successor` 分别保留对应范围的证据。原始来源逐帧记录也保留该审计。兜底父窗证明可展示窗外冲突，但父窗的存在本身不成为全局阻断条件。

## 评分与兼容边界

硬失败不生成条件通过值、正式等级或分数；现有服务政策将核心无效结果输出为 `unavailable`，没有 E/0 补值。未确认的身份模糊、候选 track switch 等原有 advisory 政策不变。数值公式、关节共同有效分母和评分阈值均未修改。缓存须同时包含 helper 政策与消费门版本。

历史直接构造、没有原多人体记录账本的 `PoseSequence` 保持已有低层数学接口行为，同时不增加“已审计成功”声明。生产记录入口重新计算账本；这不是对外部 JSON 或原媒体的数字签名验证。源记录中全部相关原始几何被一致改写的情形，不是本地序列合同能够独立证明的事项。

## 验证范围

`tests/test_temporal_pose_person_association.py` 覆盖实际量测帧、前一原帧、回升确认、兜底、后继间隔、窗外与其他人物冲突、raw/primary 编号区别、账本篡改、完整分母和正式无分结果。已提交的真实 USTA 三帧原记录重新得到拒绝列表 `[] / [2] / []`；这些导出记录缺完整 decoder 绑定，因此该用例仅证明原记录审计，不声称新视频解码或评分。

另用已提交 USTA 周期原几何注入明确标记的合成冲突，验证 FS03、FS04 都拒绝该消费窗且清除合成冲突后逐字段恢复原结果。这不是新的真实错人样本，也不是纯步型阳性。相关坐标、来源对齐、时序和周期回归与新测试共 100 项通过；本批无新模型推理。
