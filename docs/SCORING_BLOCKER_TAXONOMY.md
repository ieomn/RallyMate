# RallyMate 评分阻断类型与人工证据路由

版本：`scoring-blocker-taxonomy-v1.0.0`  
范围：固定机位、单主球员、Pose-only，当前 13 项 F2 指标。

## 为什么需要统一分类

评分输出同时包含原始 `quality_flags`、`scoring_block_flags`、稳定的 `reason_codes`、中文反馈和后续人工真值任务。过去这些映射分散在评分、就绪审计和工作清单代码中；新增 flag 时容易出现报告仍沿用旧原因、甚至把关键点跳变或目标方向缺失统称为“身份连续性”的漂移。

当前唯一机器真源为根目录的 `scoring-blocker-taxonomy.json`，Python 真源为 `src/rallymate_scoring/blocker_taxonomy.py`。评分、三视频 readiness、单视频 blocker audit、人工行动清单和真值优先级清单都读取同一映射。

## 当前类别

- `identity_continuity`：source Track 切换候选、主球员选择歧义；需要主球员身份连续性真值。
- `pose_diagnostic`：关键点跳变、左右点交换；需要完整时间线人工诊断真值。
- `tactical_context`：目标方向未观察；需要独立人工目标方向语义，不能由球员移动方向反推。
- `event_boundary` / `event_phase`：事件运动学覆盖不足、右边界截断峰、两样本阶段峰；需要人工事件或阶段边界。
- `pose_observation`：主球员 Pose 覆盖不足；需要 Pose 观测与事件边界联合复核。
- `side_semantics`：左右脚活动峰接近导致启动脚代理歧义；需要人工启动侧语义。

未知 flag 不会被猜测为现有类别，而是进入 `external_manual_review_required`。分类只说明“为什么当前不能正式评分”和“需要哪类证据”，不证明诊断 flag 本身正确。

## M78 三视频重放

M78 使用当前代码复用 M63 的三段 Halpe26 frames 和 primary timeline，无 GPU 重放事件、特征和评分：

- 546 个事件记录的键和内容一致；
- 2,366 个指标×事件实例的 `status`、`grade`、`reason_codes`、`feedback`、`quality_gate` 逐条一致；
- 特征值、单位、有效性、原因、raw/smoothed 值、版本和证据帧一致；
- 仅嵌套 confidence 的序列化末位存在最大 `0.000001` 的差异；该容差不是评分阈值；
- 总状态仍为 834 `calibration_required`、1,532 `unavailable`，grade=0、threshold=0。

机器审计：`reports/measurement-recovery-m78/blocker-taxonomy-audit.json`。

当前恢复优先级中，关键点跳变和左右点交换覆盖的评分证据阻断实例最多；但计数会重叠，不能逐行相加，也不能当作 precision、recall、准确率或人工工时估计。只有盲化人工真值完成后，才能评测某类诊断并进入受控策略审核。

## 维护规则

1. 新增 scoring block flag 时，必须先在 canonical taxonomy 中给出独立 reason、truth requirement 和 group。
2. 评分代码不得用兜底的身份原因解释非身份 flag。
3. 机器 taxonomy 必须与 Python `taxonomy_document()` 精确一致。
4. 真实产物中每个活动 flag 必须存在对应 typed reason；每个 typed reason 也必须能反查到活动 flag。
5. taxonomy 变更不得隐式修改 measurement/scoring gate；若确需改门禁，必须另起版本、真值协议和独立审核。
6. 无教练真值和独立测试时，任何分类优化都不能生成 A～E、阈值或 F3/F4 晋级。
