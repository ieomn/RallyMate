# RallyMate 三视频评分阻断归因与人工复核入口

版本：`multivideo-scoring-readiness-decomposition-v1.1.0`  
范围：固定机位、单个主球员、Pose-only，FS01/FS02/FS09 当前 13 项 F2。  
机器报告：`reports/multivideo-scoring-readiness/m65-halpe26-three-video-typed-blocker-recovery-v1/audit.json`

## 结论

M65 对 M63 三段完整视频的 2,366 条指标事件实例重新读取 `indicator-features.jsonl` 与 `scores.jsonl`，校验每条评分状态、完整特征向量、质量门禁和 `reason_codes` 完全一致。报告不会改变任何门禁，也不会生成 A～E、阈值或成熟度晋级。

互斥拆解保持不变：18 条 measurement hard fail、57 条普通测量向量不完整、180 条评分上下文不完整、1,277 条评分证据阻断、834 条仅缺教练标定和独立测试。Pose 测量层仍为 2,291/2,366 measured；这与正式评分状态必须分开解释。

## 单一阻断反事实

只有 `scoring_evidence_blocked` 且完整评分向量存在的记录进入本分析。某 flag 的“唯一阻断实例数”表示该记录没有其他活动 scoring block；它只用于安排人工真值优先级。即使该诊断未来经人工真值和预注册协议证明可以由新版策略解除，记录也只能前进到 `calibration_required`，不能直接输出 A～E。

- `keypoint_jump_candidates_present`：参与 1,025 条完整实例，是 645 条的唯一阻断。
- `left_right_swap_candidates_present`：参与 482 条，是 173 条的唯一阻断。
- `lead_foot_side_proxy_ambiguous`：参与 80 条，是 30 条的唯一阻断。
- landing/first-step 阶段代理、身份连续性和边界覆盖依次构成其余较小阻断。

全体记录的类型化原因也逐条反查原始 flag：跳点 1,152/1,152、左右交换 488/488、目标方向 182/182、身份连续性 125/125，其余边界、Pose 覆盖、启动侧与阶段原因也全部一致。这里的分母是原因出现的指标实例，不是诊断准确率。

## 三视频全时间线真值入口

M65 生成 1,516 个去重候选复核任务：三段视频分别为 134、261、1,121 项。每段都同时提供可跳转视频的 review queue，以及覆盖区间和稀疏真阳性的空白 CSV。候选页面只帮助定位，不能把未标注候选当作真值，也不能只审候选正例后计算 recall。

- `reports/pose-diagnostic-review/m65-3ae77ee3271d67de171585a5c39ddd69-halpe26-full/index.html`
- `reports/pose-diagnostic-review/m65-850cb0006b406c7176eeda8d711cd065-halpe26-full/index.html`
- `reports/pose-diagnostic-review/m65-8d7754d0de6d315674013d5b69a0b6ba-halpe26-full/index.html`

对应 truth pack 位于 `reports/pose-diagnostic-truth/m65-*-halpe26-full-v1/`。当前三段 full-timeline coverage 都是 0，precision/recall/F1 均为 `null`。聚合审查 `reports/pose-diagnostic-quality-gate-review-m65-three-video-empty.json` 状态为 `annotation_and_protocol_required`；质量策略仍为 `indicator-event-quality-v1.6.0`，没有自动变化。

## 安全边界

候选数量、唯一阻断数量和预计恢复到 `calibration_required` 的数量都不是准确率。正式策略变更必须先完成双人独立全时间线真值和裁决，再使用结果揭示前冻结的外部接受协议计算 precision/recall/F1，最后由人工审核发布新的版本化 quality policy。标定、独立测试、F3/F4 和受信任 production promotion 仍是后续独立门禁。

## 验证记录

全仓回归为 581 tests OK（16 个可选 `jsonschema` 测试跳过）；另用安装了 Draft 2020-12 校验器的环境验证 11 份 M65 JSON，0 error。三条 review queue、三份 truth pack 与多视频拆解均通过来源 SHA 重放；主页和 4 个 M65 页面共 140 个本地引用、缺失 0，6 个本地 HTTP 入口均为 200。`compileall` 与 `git diff --check` 通过。
