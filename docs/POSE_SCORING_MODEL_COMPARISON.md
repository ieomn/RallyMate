# RallyMate YOLO / RTMPose 动态评分对比

完整可播放报告：`reports/pose-scoring-ab/index.html`。

对比视频不是抽帧截图：

- `reports/pose-scoring-ab/rtmpose-wholebody133-full-detail-31s-51s-browser.mp4`：20 秒完整 WholeBody-133 分组动态输出；
- `reports/pose-scoring-ab/yolo17-vs-halpe26-vs-wholebody133-31s-51s-browser.mp4`：20 秒三模型同帧同步对比；
- `reports/pose-scoring-ab/rtmpose-halpe26-full-body-26points-31s-51s-browser.mp4`：20 秒主球员全身放大、全部 26 个实际点编号、逐帧名称/置信度与缺口说明；
- `reports/pose-scoring-ab/yolo-vs-rtmpose-keypoint-focus-31s-51s-browser.mp4`：20 秒 YOLO / RTMPose 双栏全身与左右足部放大动态视频；
- `reports/pose-scoring-ab/yolo-vs-rtmpose-scoring-highlight-40s-60s-browser.mp4`：20 秒 H.264 重点片段；
- `reports/pose-scoring-ab/yolo-vs-rtmpose-scoring-comparison-browser.mp4`：97 秒 H.264 完整同步四宫格；
- `reports/experiments/small-roi-pose-recovery-halpe256-full-v1/small-roi-pose-recovery-comparison-browser.mp4`：13 秒当前 32px 与实验 8px ROI Pose 动态 A/B；
- `reports/pose-scoring-ab/residual-computability-evidence-browser.mp4`：4.77 秒剩余 19 条 unavailable 的源视频动态证据。

- `reports/measurement-recovery-m67/roi-margin-0.30/3ae77ee3271d67de171585a5c39ddd69/current15-vs-experimental30-changed-browser.mp4`：视频 1 的 0.15/0.30 ROI margin 动态 A/B；
- `reports/measurement-recovery-m67/roi-margin-0.30/850cb0006b406c7176eeda8d711cd065/current15-vs-experimental30-changed-browser.mp4`：视频 2 的动态 A/B，包含唯一 operational 回归范围；
- `reports/measurement-recovery-m67/roi-margin-0.30/8d7754d0de6d315674013d5b69a0b6ba/current15-vs-experimental30-changed-browser.mp4`：视频 3 的动态 A/B。

- `reports/measurement-recovery-m68/required-joint-superset/3ae77ee3271d67de171585a5c39ddd69/current-vs-required-joint-router-changed-browser.mp4`：视频 1 的当前 Pose 与 required-joint 严格超集路由动态对比；
- `reports/measurement-recovery-m68/required-joint-superset/850cb0006b406c7176eeda8d711cd065/current-vs-required-joint-router-changed-browser.mp4`：视频 2 的动态路由对比；
- `reports/measurement-recovery-m68/required-joint-superset/8d7754d0de6d315674013d5b69a0b6ba/current-vs-required-joint-router-changed-browser.mp4`：视频 3 的动态路由对比。

- `reports/measurement-recovery-m69/high-resolution-384-margin030/3ae77ee3271d67de171585a5c39ddd69/current-m68-vs-halpe384-margin030-router-changed-browser.mp4`：视频 1 的 M68 当前 Pose 与 384×288/0.30 ROI 残差路由动态对比；
- `reports/measurement-recovery-m69/high-resolution-384-margin030/850cb0006b406c7176eeda8d711cd065/current-m68-vs-halpe384-margin030-router-changed-browser.mp4`：视频 2 的残差路由动态对比；
- `reports/measurement-recovery-m69/high-resolution-384-margin030/8d7754d0de6d315674013d5b69a0b6ba/current-m68-vs-halpe384-margin030-router-changed-browser.mp4`：视频 3 的残差路由动态对比。

- `reports/measurement-recovery-m70/m69-anchored-extension-v1/3ae77ee3271d67de171585a5c39ddd69/m69-vs-anchored-profile-extension-changed-browser.mp4`：视频 1 的 M69 与 M70 锚定扩展动态对比；
- `reports/measurement-recovery-m70/m69-anchored-extension-v1/850cb0006b406c7176eeda8d711cd065/m69-vs-anchored-profile-extension-changed-browser.mp4`：视频 2 的 M69 与 M70 锚定扩展动态对比。视频 3 未选择扩展帧，未生成伪变化视频。

- `reports/measurement-recovery-m71/rtmpose-l-384-context-extension-v1/3ae77ee3271d67de171585a5c39ddd69/m70-vs-rtmpose-l-extension-changed-browser.mp4`：视频 1 的 M70 与 RTMPose-L 受控扩展动态对比；
- `reports/measurement-recovery-m71/rtmpose-l-384-context-extension-v1/850cb0006b406c7176eeda8d711cd065/m70-vs-rtmpose-l-extension-changed-browser.mp4`：视频 2 的动态对比。视频 3 未选择 L 候选帧，未生成伪变化视频。

- `reports/measurement-recovery-m72/additive-keypoint-fusion-v1/3ae77ee3271d67de171585a5c39ddd69/m71-vs-additive-keypoint-fusion-changed-browser.mp4`：M71 与 M72 同拓扑缺失点加法融合动态对比；仅该视频产生完整指标恢复。

- `reports/measurement-recovery-m73/wholebody133-mapped-fusion-v1/3ae77ee3271d67de171585a5c39ddd69/m72-vs-wholebody133-mapped-changed-browser.mp4`：M72 与 WholeBody133 显式同名点映射动态对比（视频 1）。
- `reports/measurement-recovery-m73/wholebody133-mapped-fusion-v1/850cb0006b406c7176eeda8d711cd065/m72-vs-wholebody133-mapped-changed-browser.mp4`：同一合同的视频 2 动态对比；101 个总补点没有新增完整指标。

当前正式评分门禁：注册表共 13 项，层级为 F2；人工事件 0 条、人工校正关键点 0 条、人工语义 0 条、教练标注 0 条、可拟合样本 0 条、生产标定资产 0 个。因此现在能跑通事件/特征/质量状态契约，但不能正式输出 A～E，状态必须保持 `calibration_required` 或 `unavailable`。人工入口为 `data/annotations/scoring-truth-pack-v1/review.html`，当前三视频标定组合见 `reports/scoring-truth-calibration-portfolio/m53-empty-registry-17-three-video-v1/index.html`。

当前版本绑定：registry `pose-wave-2026-08-22.17`，scoring loop `minimum-scoring-loop-v0.6.0`，primary `primary-player-v0.3.0`，event `pose-motion-bout-v0.4.1`，FS01/FS02 `fs01-fs02-pose-proxies-v0.5.0`，FS09 `fs09-pose-proxies-v0.2.0`，quality `indicator-event-quality-v1.6.0`。97 秒全片 M53 使用既有 Pose/Track 做当前版本无 GPU 后处理重算；M42 GPU Worker 是 `.15` 历史部署烟测，同窗/固定边界模型 A/B 也保留为历史比较快照。

历史 31–51 秒窗口曾为 Halpe26 与 WholeBody133 生成 registry 声明的 13 项指标特征与安全状态：Halpe26 特征层为 130/130 条 `measured`，评分层为 69 条 `calibration_required`、61 条 `unavailable`；WholeBody133 特征层为 143/143 条 `measured`，评分层为 44 条 `calibration_required`、99 条 `unavailable`；两边均为 0 个 A～E、0 个伪阈值。事件候选差异只能说明规则边界受 Pose 输出影响，不能当作准确率或正式评分能力，也不能冒充当前 `pose-wave-2026-08-22.17` 评分向量产物。机器对照见 `reports/fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json`。

Pose 诊断现已生成可跳转连续视频的去重人工复核队列：Halpe26 视频 3ae77e 全片 134 个去重任务、94 个候选帧、151 个受影响指标实例；Halpe26 视频 850cb0 全片 261 个去重任务、198 个候选帧、240 个受影响指标实例；Halpe26 视频 8d7754 全片 1121 个去重任务、699 个候选帧、974 个受影响指标实例。浏览器只导出人工复核 CSV，所有条目仍为 pending candidate，不是真值，也不会自动修改质量门禁。入口位于 `reports/pose-diagnostic-review/`。

为避免只看正候选导致无法测漏检，另生成全时间线诊断真值包：Halpe26 视频 3ae77e 全片 1441 帧、4 类诊断、覆盖/真阳性均为 0，状态 annotation_required；Halpe26 视频 850cb0 全片 2911 帧、4 类诊断、覆盖/真阳性均为 0，状态 annotation_required；Halpe26 视频 8d7754 全片 11516 帧、4 类诊断、覆盖/真阳性均为 0，状态 annotation_required。工作台采用覆盖区间 + 稀疏真阳性；无人工裁决时 precision/recall/F1 必须保持 null，入口位于 `reports/pose-diagnostic-truth/`。

诊断质量策略审查绑定 3 个完整视频范围、4 类诊断，当前为 `annotation_and_protocol_required`。缺少外部预注册接受协议和全时间线真值时，质量门禁不会改变；未来即使指标满足协议，也只进入人工策略审核。机器报告为 `reports/pose-diagnostic-quality-gate-review-m65-three-video-empty.json`。

候选事件跨模型全局匹配在 IoU≥0.3 时为 30 对，平均 Segment IoU=0.9151；这不是 Event F1。固定 Halpe 候选边界时双方有效 required feature 对为 559/560；固定 WholeBody 候选边界时为 616/616。`hip_center_y_body` 跨模型 MAD 分别为 0.652/0.747 body，因此当前不能据颗粒度直接完成生产模型替换或正式评分。

97 秒全片模型路由审计使用同一套 Halpe256 候选边界：Halpe256 为 403/442、Halpe384 为 399/442、WholeBody133 为 369/442 个指标事件特征完整。Halpe384 在各自切分边界上为 415/442，但与 Halpe256 的自动事件在 IoU≥0.3 时只匹配 90/102，中心边界平均绝对差 64.26 ms，因此不能把各自切分数字当模型净提升。当前保留 Halpe256 作为评分主配置，禁止逐事件/逐特征跨模型拼接；这只是覆盖路由，不是准确率结论。机器报告为 `reports/pose-profile-routing-audit-full.json`。

全片 266 条 `unavailable` 已按完整 scoring vector 做互斥归因：194 条评分向量完整、仅被正式评分证据阻断；3 条仅评分向量不完整；66 条评分向量不完整且同时有 score-only 阻断；3 条 hard fail。评分向量不完整包含 FS02-M02 人工目标方向缺失，不等于 Pose 测量失败。若相关人工真值与受控策略门禁未来都通过，完整向量的 194 条最多恢复为 `calibration_required`，不会直接生成等级。M43 机器报告为 `reports/scoring-blocker-audit-halpe256-full-m43.json`。

M43 类型化原因逐条反查 raw gate：跳点 201、左右交换 69、目标方向 34、身份连续性 43 条，均无多报或错归因。历史 M31 视频工作清单有 129 项，但早于 M42 scoring vector，不再作为当前计数真源。

M53 当前视频行动清单覆盖 266/266 条 unavailable，收敛为 168 个 event×真值要求、481 条实例×动作关联；Pose 诊断/关键点特征/目标方向/事件阶段分别 92/11/34/31 项，Pose 队列缺失关联 0。入口为 `reports/scoring-truth-action-worklist-halpe256-full-m53/index.html`；它不自动解除门禁或产生等级。

M45 行动证据状态机对 M44 的每个 work_item 与受影响指标实例逐项重算：当前证据满足/仅复核未裁决/仍需标注为 0/0/168，全部关联动作证据满足的指标实例为 0/266。它重新计算事件匹配、特征误差与 Pose 全时间线 truth CSV，拒绝自报状态；完成任务不自动改 score/quality，也不生成 grade/threshold。机器报告为 `reports/scoring-truth-action-readiness-halpe256-full-m45.json`。

M46 将这 168 个 work item 的共享依赖折叠为 87 个证据获取单元（当前满足 0）：4 个完整时间线 Pose 单元优先，其余精确绑定事件边界、阶段、密集关键点、特征真值、侧别语义和目标方向。入口为 `reports/scoring-truth-evidence-plan-halpe256-full-m46/index.html`；优先级按影响实例数排序，不是准确率排名，也不会自动解除门禁。

M47 最新不可变刷新 `m53-empty-registry-17-v1` 把 truth compile、事件/特征误差、Pose 诊断误差、M45 与 M46 放在同一 hash-bound bundle；人工事件/关键点帧/语义/教练标签为 0/0/0/0，work item 与证据单元满足数为 0/168、0/87。只有全链验证通过才原子更新 `reports/scoring-truth-refresh/latest.json`；该刷新不运行 A～E 评分或成熟度晋级。入口为 `../scoring-truth-refresh/m53-empty-registry-17-v1/index.html`。

M48 标定交接 `m53-empty-registry-17-v1` 从 M47 冻结的人工边界重算 manual-event features，并以精确 event_id 编译 13 项 prepared dataset：人工特征 0、samples 0、fit-ready 0/13。当前 13/13 prepared 文件结构有效但全部被拟合前门禁拒绝；未调用候选事件检测器或拟合器，也未生成候选资产、阈值、模型、等级或 F3/F4。入口为 `reports/scoring-truth-calibration-handoff/m53-empty-registry-17-v1/index.html`。

M49 三视频标定组合 `m53-empty-registry-17-three-video-v1` 覆盖真值包 3/3 段视频、15868 帧已有 RTMPose-M/Halpe26 结果，并统一使用当前主球员时序。每段只接受人工事件边界重算特征，再汇总为同一 13 项 calibration dataset；当前人工事件/特征/samples 为 0/0/0，fit-ready 0/13。本轮复用既有 Pose 帧、未运行 GPU/候选事件检测/拟合，也未生成阈值、模型、等级或 F3/F4。入口为 `reports/scoring-truth-calibration-portfolio/m53-empty-registry-17-three-video-v1/index.html`。

M63 三视频计算覆盖 `m63-halpe26-three-video-fs02-m05-first-step-evidence-v1` 对 3 段、15868 帧既有 Pose 运行当前 registry 的候选事件与 13 项特征合同：546 个候选事件、2366 条指标实例中，2291 条特征 measured、75 条 unavailable；13/13 指标在每段视频均至少有一条完整向量。评分状态仍为 834 calibration_required / 1532 unavailable，grade=0、threshold=0。候选事件未做人工真值评测，计算覆盖不是准确率。入口为 `reports/multivideo-indicator-calculation-coverage/m63-halpe26-three-video-fs02-m05-first-step-evidence-v1/index.html`。

M65 就绪拆解 `m65-halpe26-three-video-typed-blocker-recovery-v1` 将同一 2366 条实例按最先阻断层互斥重放：measurement hard fail 18、普通测量向量不完整 57、评分上下文不完整 180、评分证据未验证 1277、仅缺标定/独立测试 834。原始 Pose 向量完整 2297，通过测量门禁 2291，完整评分向量且通过评分证据门禁 834。该审计不补 0、不解除门禁、不生成等级或阈值，入口为 `reports/multivideo-scoring-readiness/m65-halpe26-three-video-typed-blocker-recovery-v1/index.html`。

M65 在上述三视频拆解上增加类型化原因反查和单一阻断反事实：`keypoint_jump_candidates_present` 参与 1025 条完整评分向量，并且是 645 条的唯一活动阻断；全部类型化原因均有原始 scoring block flag 支撑。系统为三段完整视频生成 1,516 个去重 Pose 诊断复核任务和空白全时间线 truth pack，当前 coverage=0、precision/recall/F1=null、quality policy 未改变。

M78 把该映射正式收敛为 `scoring-blocker-taxonomy-v1.0.0`：每个 scoring block flag 只路由到对应的 typed reason、人工真值要求和 blocker group，跳点/左右交换/目标方向不会再被误写成身份连续性。无 GPU 重放 3 段、2366 个指标实例后，全部非 confidence 载荷和 score status/grade/reason/feedback/quality gate 逐条一致；仅 258 个 confidence 叶值有最大 0.000001 的序列化末位差。它不改 gate、不解除 unavailable、不生成 A～E 或准确率结论。机器审计为 `reports/measurement-recovery-m78/blocker-taxonomy-audit.json`。

M80 将跳点/左右点交换的 1371 个候选冻结为真正的两阶段视频真值流程：103 个候选窗口分别编码为独立 H.264 MP4，覆盖 9636 帧与 1336 个去重指标实例。两名不同 annotator 在不显示候选帧、关节或骨架的页面独立导出原始 CSV，第三名 reviewer 才能在隔离页查看密封候选和 103 份逐帧模型骨架/目标关节轨迹；模型叠加明确不是真值。当前 coverage/positive/adjudication 为 0/0/0，所以 candidate precision=null；候选窗口不具备完整时间线 recall/F1，且不会自动改 gate、grade、threshold 或 F3/F4。入口为 `data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2/review.html`。

M81 把当前 3 段完整视频、15868 帧 Halpe26、546 个候选事件和 2366 条指标实例放入一个同步播放器：骨架、事件阶段、13 项指标、特征值、证据帧与质量门禁随视频联动。特征状态为 2291 measured/75 unavailable，评分状态为 834 calibration_required/1532 unavailable；grade=0、threshold=0。入口为 `reports/scoring-visual-observer-m81/index.html`。

M63 平滑反事实覆盖审计绑定 registry `pose-wave-2026-08-22.17` 与三段 M63 features SHA：9971 条有效 Pose 特征记录中 9965 条可由序列化 raw evidence 无歧义重放无平滑对照（99.94%），51 个 Pose required feature 中 45 个全覆盖、6 个部分覆盖、0 个完全未覆盖。FS02-M05 的启动脚速度下降/第一步位移/髋方向一致性/启动脚减速到髋方向建立时差/步后站距分别为 179/180、179/180、180/180、180/180、179/179 可重放。前两项各一条 raw 轨迹无法形成正向启动侧，继续 unavailable；事件阶段固定为 production <code>first_step_slowdown_proxy_ms</code>，不随 raw 重放漂移。该覆盖不是 MAE/准确率，也不是可加和误差分解；人工真值、F2、grade=0 与 threshold=0 均不变。机器报告为 `reports/smoothing-counterfactual-coverage-m63.json`。

M64 误差预算就绪矩阵把当前 registry、M63 三视频计算覆盖、平滑反事实和三段当前真值评测逐源重放：10/13 项的全部 Pose 特征平滑证据完整，3/13 项仍有少量 raw partial；人工事件/关键点/语义均为 0，因此 Event F1/IoU/Boundary MAE、特征 MAE/P95/Bias、缺失值误差影响和外部等级间距判断仍未评测，F2→F3 ready 为 0/13。该矩阵不把平滑敏感性或测量覆盖当准确率，不生成 grade/threshold，机器报告为 `reports/f2-error-budget-readiness-m64.json`。

剩余 39 条 feature unavailable 已追到 11 个候选事件、138 个唯一 event×feature 缺口；其中 134 个是事件内有效 Pose 覆盖不足。固定相同候选边界时，Halpe384 只恢复当前缺口中的 2 条并另丢失 6 条，WholeBody133 恢复 0 条并另丢失 34 条，因此继续禁止逐事件/逐特征跨模型拼接。机器报告为 `reports/feature-observation-gap-audit-halpe256-full.json`。

小 ROI 实验保持同一视频、检测、主球员时间线、模型权重和 102 个候选事件，只把最小 ROI 从 32px 改为 8px：131/131 个目标帧得到 Pose，固定边界特征完整实例从 403/442 增至 423/442，恢复 20 条、回归 0 条。这只证明实验可观测性，不证明远场关键点准确；生产默认仍为 32px，A～E 和成熟度未改变。机器报告与动态视频位于 `reports/experiments/small-roi-pose-recovery-halpe256-full-v1/`。

M66 将同模型小 ROI 实验扩展到当前三视频机器真源，并把 Pose 特征向量状态与原事件/身份测量门禁后的 operational 状态分开。当前生产为 2291/2366 measured、75 unavailable；149 个合格目标帧全部产生实验 Pose，恢复 22 条特征向量，但保留原质量门禁后只恢复 20 条指标实例，实验投影为 2311/2366 measured、55 unavailable，回归 0。生产 32px 默认、18 条 hard fail、grade=0、threshold=0 均不变；149/149 是可观测性，不是关键点准确率。汇总与两段动态 A/B 位于 `reports/measurement-recovery-m66/`。

M67 在同一 RTMPose-M Halpe26 256×192 权重、检测、Track、事件与质量门禁上评测 0.15→0.30 ROI margin。410 个目标帧全部产生 Pose，特征向量恢复 14 条/回归 1 条，门禁后恢复 11 条/回归 1 条；有效关键点计数增加/减少/不变为 76/107/227 帧。与 M66 的互斥目标集合合入完整帧后实际重算，特征完整实例为 2331/2366，门禁后 measured 为 2320/2366，净恢复 29 条，hard fail 仍为 18。由于真实发生 1 条回归且人工关键点/独立视频发布测试仍缺，生产保持 0.15 margin、32px 最小 ROI、无自动 fallback；grade=0、threshold=0、F2 不晋级。汇总为 `reports/measurement-recovery-m67/summary/report.json`。

M68 将 margin 候选限制为注册表所需 8 个肩/髋/膝/踝关节的有效集严格超集：候选不得丢失任何当前有效评分关节，选择过程不读取特征值、事件结果、grade 或 threshold。410 个 margin 候选帧只选中 44 个，拒绝 366 个；与 M66 合并后的真实路由帧为 193 个。固定边界重算后，特征完整实例由 2297/2366 提升为 2329/2366（恢复 32、回归 0），门禁后 measured 由 2291 提升为 2320（恢复 29、回归 0）。它在当前三视频保留 M67 的净 operational 增益并消除当前集合回归，但这不是人工关键点准确率或独立发布验证；生产配置、F2、grade=0、threshold=0 均不变。汇总为 `reports/measurement-recovery-m68/summary/report.json`。

M69 在 M68 后 28 条非 hard-fail 特征残差、13 个事件内，仅对 258 个目标帧运行 RTMPose-M Halpe26 384×288/0.30 ROI 候选；255 帧得到 Pose，但 required-joint 严格超集门禁只选中 40 帧。固定边界重算后特征完整实例由 2329/2366 提升为 2335/2366，门禁后 measured 由 2320 提升为 2326，均恢复 6、回归 0；非 hard-fail 残差由 28 降到 22。候选同时改变权重/输入分辨率与裁剪上下文，不能单因子归因；没有人工关键点误差与独立视频测试，因此不改生产、F2、grade=0 或 threshold=0。汇总为 `reports/measurement-recovery-m69/summary/report.json`。

M70 用 M68 每帧真实裁剪来源重放同一 258 个残差帧，将 384×288 Pose profile 与 ROI 上下文分离。只换 profile 恢复 2 个 operational 实例；M69 统一上下文恢复 6 个。直接多候选择优会丢失 1 个 M69 已恢复实例，因此拒绝；以 M69 为锚、只接受必需关节严格超集的扩展选择 15 帧，特征完整实例达到 2338/2366，门禁后 measured 达到 2327/2366，相对 M68 恢复 7、回归 0，相对 M69 额外恢复 1。这仍不是准确率或生产发布证据；F2、grade=0、threshold=0 不变。汇总与动态视频位于 `reports/measurement-recovery-m70/`。

M71 将官方 RTMPose-L Halpe26 384×288 仅运行在上述 258 个残差帧，并从 M70 出发仅接受 required-joint 有效集严格超集。三视频共选择 46 帧；相对 M68 特征完整实例达到 2344/2366（恢复 15、回归 0），门禁后 measured 达到 2333/2366（恢复 13、回归 0）；相对 M70 再新增 6 个完整特征实例和 6 个 operational 实例，丢失 M70 恢复为 0。该结果证明少量残差帧的可观测性增益，不证明关键点准确率或模型已可生产替换；F2、grade=0、threshold=0 不变。汇总与动态视频位于 `reports/measurement-recovery-m71/`。

M72 不再替换整副 Pose，只把同一 Halpe26 拓扑中 M71 无效、RTMPose-L 有效的评分相关点加到 M71，并逐点证明所有 baseline-valid 坐标未覆盖。46 帧共新增 79 个有效关节观测，但固定边界只新增恢复 2 个完整特征/operational 实例，最终为 2346/2366 feature complete、2335/2366 operational measured，回归与 M71 恢复丢失均为 0。剩余 13 个非 hard-fail 缺口分为事件观测覆盖不足 9、视频起始边界截断 2、所需 FS09 Pose 阶段未观察到 2。不能靠降低有效率门槛、补 0 或伪造阶段消除；融合也必须经人工逐点误差与独立视频验证。生产、F2、grade=0、threshold=0 不变，机器汇总在 `reports/measurement-recovery-m72/summary/report.json`。

M73 进一步使用官方 WholeBody133 做跨拓扑显式同名点补充，仍逐点保持 M72 的全部有效 Halpe26 观测。258 个冻结目标帧中 254 个产生候选 Pose，49 帧新增 101 个有效点，但新增 feature complete 与 operational measured 都是 0；最终仍为 2346/2366 和 2335/2366。这个负结果是有价值的：更密拓扑没有解决剩余事件内连续覆盖、视频起点截断或 FS09 阶段代理缺失，不能继续把“点更多”当成评分能力。机器汇总在 `reports/measurement-recovery-m73/summary/report.json`；生产、F2、grade=0、threshold=0 不变。

M74 对 M73 剩余 13 个实例做事件内 timestamp 缺口审计：只允许同一事件内两个真实有效端点夹住、端点跨度≤160 ms 的线性插值，不允许首尾外推、跨事件借帧、补 0 或生成 phase。去重后有 142 个 event×joint×frame 候选点，反事实可使 7/13 个特征向量完整，仍有 6/13 不完整。由于这些点不是模型观测且尚无人工关键点真值，production recovered 固定为 0，所有原实例保持 unavailable；机器逐点证据为 `reports/measurement-recovery-m74/event-bounded-gap-audit-v1/report.json`。

M75 已把上述去重候选冻结为 142 个关节点任务，覆盖 2 段视频、54 帧；插值坐标保存在密封 JSONL 中，不进入浏览器 bootstrap。每点需要两名标注者独立作答及未参与标注的 reviewer 裁决。当前人工标注 0、接受裁决 0，所以 MAE/P95/Bias/valid rate 与分视角结果均为 null，生产插值仍关闭。入口为 `data/annotations/event-bounded-pose-gap-truth-m75-v1/review.html`，空白误差报告为 `reports/measurement-recovery-m75/event-gap-keypoint-error-empty.json`。

M76 已把该裁决合同接入真值条件特征重放：只替换 M75 缺口坐标，保留非缺口 Pose、候选事件边界和 Track。固定范围为 11 个指标实例、47 条 required-feature 记录和 25 个唯一特征；2 个 FS09 phase-only 残差不使用插值伪造。当前人工裁决为 0，因此特征 MAE/P95/Bias/valid rate 及分特征/分指标结果均为 null，生产插值、A～E、threshold 和 F3/F4 晋级全部关闭。机器报告为 `reports/measurement-recovery-m76/event-gap-feature-error-empty.json`。

M77 已把 M76 显式排除的 2 个 FS09-M05 phase-only 残差变成无骨架视频盲标任务。这两段事件的髋/踝 Pose 覆盖完整，但 `hip_deceleration_to_double_support_proxy_ms` 找不到双踝同时低运动代理起点。M88 在 0 标签状态下把真人执行入口收口为候选隔离、内容寻址、可移动的公开盲包：`data/annotations/fs09-phase-truth-m77-v1/` 是不得分发或服务的私有权威包，A/B/C 只接收 `data/annotations/fs09-phase-truth-m77-blind-handoff-v1/`。公开包只含计划、工作台、匿名说明、allowlist Range server 和两段 H.264 短片；每行导出绑定 handoff/plan，判断时间与导出时间分离，中央 intake 再逐字节绑定私有谱系。每个任务需双人独立标注 FS09 存在性、边界和 4 个可见阶段，再由第三人裁决。当前人工标注 0、接受裁决 0，所以 Event IoU/Boundary MAE、phase MAE 和边界条件特征差均为 null。人工稳定控制不会被当成双支撑/接触/受力真值；如果 Pose 代理仍缺失，目标特征继续 unavailable。空白报告为 `reports/measurement-recovery-m77/fs09-phase-truth-empty.json`。

M35 已把上述 131 个恢复帧冻结为 1834 个关节点盲标任务：两名独立标注者分别作答，第三名 reviewer 裁决，UI 不显示或预填模型坐标。当前人工标注 0、裁决 0，因此像素/归一化 MAE、P95、Bias 和 PCK 均不计算，生产 32px 路由不变。入口为 `data/annotations/small-roi-keypoint-truth-halpe256-v1/review.html`，空白误差报告为 `reports/small-roi-keypoint-error-halpe256-v1-empty.json`。

M36 可计算性证书逐项验证了 registry required feature 的值、单位、版本、置信度和证据帧：13/13 个指标均有真实 measured 实例，每项 30～34 条，总计 423/442。剩余 19 条由画面边界裁切 12、source Track 过渡 6、视频起始边界 1 构成，继续 fail closed；机器证书为 `reports/residual-indicator-computability-small-roi-v1.json`，动态证据为 `reports/pose-scoring-ab/residual-computability-evidence-browser.mp4`。这不代表事件/特征准确或 A～E 可评分。

M37 已把正常上传的 Pose 默认从 YOLO COCO-17 切换为 `rtmpose-m-halpe26-online`，同时保留 `yolo-baseline` 显式回滚。未设置环境 preset、也未传 CLI override 的真实 GPU Worker 烟测处理 120 帧，30.822 FPS，bundle passed；其模型权重与全片证据 SHA 一致。保持生产 32px ROI 时，全片 403/442 条特征实例 measured，13/13 指标各有 28～33 条真实可测实例。默认替换只推进 F2 测量入口，不推进 F3/F4，不生成 grade/threshold；机器审计为 `reports/default-pose-routing-audit-m37.json`。

M38 的 `indicator-calculation-readiness-v1.0.0` 已迁移到当前 M42 产物：按 registry 精确列出每项 measurement event/feature、候选实例、measured/unavailable、证据帧、特征失败、measurement hard flag、独立 scoring-only flag 和不放宽门禁的补救动作。M42 真实 GPU Worker 为 13/13 项至少有 measured 候选、36/39 条 measured；97 秒全片为 13/13、403/442。不足不会显示成 0 分，报告位于 `runs/rtmpose-m-halpe26-default-m42-scoring-vector-smoke/calculation-readiness.json` 与 `reports/fs09-pose-wave-v2-m42/850cb0006b406c7176eeda8d711cd065/calculation-readiness.json`。

M39 的 `indicator-measurement-portfolio-v1.0.0` 已迁移到当前 M42：把多事件 JSONL 收敛为每项一个可直接消费的代表 F2 Pose 测量向量。选择规则不读取动作数值、不按运动表现挑最好动作、也不跨模型拼接。M42 真实 GPU Worker 为 13/13 项有代表测量；97 秒全片为 13/13，每项都含实际值、单位、版本、事件、Track 与证据帧，grade/threshold 仍为 0。

M40 新增 `scoring-cycle-measurement-v1.0.0`：FS01.initiation 精确连接 FS02.start，FS02 与 FS09 共享 peak_speed，并要求同一 Track/检测来源。全片 34 个周期中 25 个在同一次动作内 13/13 项全部 measured；代表周期为 13/13 特征完整、12/13 评分上下文通过。120 帧真实 Worker 有 3 个周期，最佳 13/13 特征完整、8/13 评分上下文通过，系统不跨动作补齐。选择只看完整度和观测/评分上下文质量，不看动作数值；grade/threshold 仍为 0。机器产物为 `runs/rtmpose-m-halpe26-default-m40-smoke/scoring-cycle-measurement.json` 与 `reports/scoring-cycle-measurement-halpe256-full.json`。

M41 新增 `scoring-reference-context-v1.0.0`：FS02-M02 可消费按视频 SHA 与事件边界绑定的教练/操作员目标方向，并计算 `target_direction_alignment_error_deg`；已接受记录要求观察者与独立复核者不同。当前真实全片工作清单含 34 个 FS02 事件，全部 pending、available=0，因此继续 unavailable；系统明确没有把运动方向当作战术目标，且 grade/threshold 均为 0。

M42 把 Pose 测量向量和完整评分/标定向量正式分离：FS02-M02 的 4 个 Pose 特征可以独立 measured，但评分向量还必须包含 `target_direction_alignment_error_deg`。当前 GPU 120 帧 13/13 指标有 measured 候选、36/39 指标实例测量完整，1 个同周期 13/13 测量完整；3 个 FS02 目标仍 pending，所以完整评分向量不允许丢弃目标特征。97 秒全片为 403/442 测量完整、176 calibration_required / 266 unavailable；grade=0、threshold=0。

M43 将 `target_direction_alignment_error_deg` 纳入人工特征误差评测：同一入口可用人工事件边界、人工校正 Pose 和独立复核的 image-plane target 计算 MAE/P95/Bias、分视角结果，以及 Pose/边界/平滑/缺失四类误差预算。当前人工 semantic 记录 0 条、context truth complete=false，所以相关误差仍为 null，F2 不晋级。机器报告为 `reports/truth-pack-empty-evaluation.json`。

- YOLO 6 项指标 unavailable：50/1,182（4.23%）。
- RTMPose-S：39/1,218（3.20%）。
- RTMPose-M 256：28/1,218（2.30%）。
- RTMPose-M 384：28/1,212（2.31%）。
- YOLO 踝角可测率 0%；RTMPose-M 256 左右均 97.70%。
- 足部聚焦片段的 RTMPose 主球员新增六点在 600/600 帧均通过当前点置信度门槛；这只是输出有效性，不是关键点准确率。
- 全身 26 点片段中 600/600 帧的所有 26 点均通过当前点置信度门槛；它是当前 Halpe26 输出完整性，不代表评分标准拓扑完整或像素准确。
- 所有有效结果仍为 calibration_required；原 298 项静态卡不在本轮最小闭环，不显示为数字 0。

在复用完全相同的 591 个 YOLO 候选事件区间、只比较 Pose 对特征有效性的影响时，unavailable 分别为：YOLO 50/1,182（4.23%）、RTMPose-M 256 15/1,182（1.27%）、RTMPose-M 384 14/1,182（1.18%）。因此 384 档相对 YOLO 减少约 72%，但这仍是候选事件上的特征可测率，不是人工事件真值或评分准确率。

2026-08-13 已用三套实际服务预设走通 `JobDatabase -> PersistentVisionRunner -> process_one -> run_pipeline`：Halpe26 Online 256×192 120 帧 29.976 FPS，26 条指标事件记录（7 calibration_required / 19 unavailable）；Halpe26 Analysis 384×288 120 帧 24.900 FPS，26 条指标事件记录（6 calibration_required / 20 unavailable）；WholeBody133 Analysis 256×192 120 帧 23.240 FPS，39 条指标事件记录（9 calibration_required / 30 unavailable）。三档都由当前 v2 注册表动态派生 13 项指标，grade=0、threshold=0、bundle passed；该烟测只证明部署和安全状态契约跑通，`accuracy_claim=false`。详见 `docs/POSE_DEPLOYMENT_PROFILES.md`。

二维踝角只是 Halpe26 足部颗粒度诊断，不是三维踝背屈真值或 A～E 标准。
