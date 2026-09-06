# RallyMate 最小可行评分闭环验收记录

> **验收历史账本，不是当前状态快照（2026-08-30 复核）**：本文按里程碑持续追加，正文
> 保留多个时代的 loop 版本和测试计数。当前版本、能力边界与最新全仓测试结果以
> `RallyMate技术架构与实现说明书_当前态_v1.0.md` 为准；不得从本文任一旧“当前”段落
> 单独推导生产评分状态。

当前代码版本：`minimum-scoring-loop-v0.7.0`；本账本早期章节始于 `v0.4.23`  
范围：固定机位、单个主球员、Pose-only；仅 FS01/FS02/FS09。当前 `metric-feasibility-pose-wave-v2.json` 覆盖 13 项 F2：FS01-M02/M03/M04/M05、FS02-M02/M03/M04/M05、FS09-M01/M02/M03/M04/M05。

## 1. 验收结论

工程闭环已完成到 13 项 F2，并具备导入真值后执行事件误差、特征误差、标注者一致性和单指标标定推理的接口。当前没有人工事件/关键点真值、教练标签或独立测试结论，因此 13 项都没有晋级 F3/F4。必须分开解释两个状态：`feature_status=measured` 只说明候选事件上的特征向量已计算；`scoring_status=unavailable` 还可能来自更严格的身份连续性、关键阶段或事件质量门禁，不能反推为特征计算失败。仓库没有生产 A～E 阈值或获批序数模型参数。

正常上传/常驻 Worker 的 Pose 默认现为 `rtmpose-m-halpe26-online`，YOLO Pose 仅作为显式回滚；YOLO person detection 仍保留。无 preset 覆盖的真实 GPU Worker 已通过当前 13 项和 bundle 验证。生产 32px ROI 的 97 秒全片固定边界证据为 403/442 条特征实例 measured，并且 13/13 指标各有 28～33 条真实 measured 实例。该默认替换只属于 F2 测量部署验收，不改变下表的事件真值、关键点误差、标定和 F4 门禁。

每个新 Pipeline 运行还会生成 `calculation-readiness.json`，按注册表逐项回答“本视频是否至少存在一个完整可测候选”，并把 Pose measurement 失败、完整 scoring vector 失败与 score-only 真值阻断分开。当前 M42 真实 GPU 120 帧烟测为 13/13 项、36/39 条指标事件实例 measurement measured；97 秒全片为 13/13 项、403/442 条 measured。短窗口缺少完整动作不会被显示为 0 分，Pose 可测也不等于完整评分向量可用或正式 A～E。

| 验收项 | 结论 | 证据 |
|---|---|---|
| FS01/FS02/FS09 可输出可评测区间 | passed_as_candidate_baseline | `events.jsonl`、`contracts/events.schema.json`、事件评测器 |
| 13 指标输出版本化特征/单位/有效性/证据帧 | passed_at_F2 | `features.jsonl`、`indicator-features.jsonl`、58 个版本化特征定义；数量不代表准确率 |
| 人工真值事件/特征误差 | interface_and_tests_passed | `evaluate_scoring_truth.py`、合成真值单测 |
| 无真值保持安全状态 | passed | 当前 Halpe26/WholeBody133 同窗结果 grade 全空、阈值版本全空 |
| 有标定数据后统一 A～E/unavailable 契约 | interface_and_tests_passed | 阈值规则与序数回归合成标定单测；无生产参数 |
| F0～F4 可逐级推进且阻断明确 | passed | `metric-feasibility-pose-wave-v2.json` 13 项 F2、blockers/acceptance metrics |
| 全链路追溯 | passed | video/frames SHA、primary/source Track、event/feature/model/registry version |

## 2. 当前 13 项同窗结果

当前机器事实以 `reports/fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json` 为准。两模型复用同一 `primary-player-v0.3.0` 主球员时间线，并处理同一 600 帧、31,000–50,966 ms 窗口：

- Halpe26：FS01/FS02/FS09 各 10 个候选事件，130 条指标记录；特征层 130 条 `measured`、0 条 `unavailable`；评分层 69 条 `calibration_required`、61 条 `unavailable`。
- WholeBody133：FS01/FS02/FS09 各 11 个候选事件，143 条指标记录；特征层 143 条 `measured`、0 条 `unavailable`；评分层 44 条 `calibration_required`、99 条 `unavailable`。
- 两边均为 `grade_count=0`，全部 threshold version 为空，比较文件显式登记 `accuracy_claim=false`。
- Event F1、Segment IoU、Boundary MAE、特征 MAE/P95/Bias 仍为 `ground_truth_required`；候选事件数、质量门禁通过数和特征可用数都不能替代准确率。

当前主球员输入位于 `reports/pose-scoring-current-inputs/primary-player-v0.3.0/`。完整 2,911 帧时间线由全片 Halpe26 frames 一次性选择，再切出 processed index 930–1529 供两模型共享；同窗两套 frames 的 frame metadata 与 detection/track/bbox 在 600/600 行完全一致，Pose 关键点仍来自各自模型。全片诊断覆盖 2,901 个可评估身份帧，其中 2,670 帧存在可比较候选，严格 score tie 为 0，最小/中位选择 margin 为 0.049733216/6.860381736；source Track switch 候选 20 次，确认 ID Switch 因无身份真值保持 `null/ground_truth_required`。另有左右交换候选 39 帧、跳点候选 190 帧、最长 Pose 缺失 88 帧/2,933 ms。v0.3 与 v0.2 除 `selection_algorithm_version` 外逐行选择结果 0 差异；v0.3 新增的是逐关节 jump/swap provenance。quality v1.6 只在候选异常关节与指标 required feature 的关节依赖相交时阻断正式评分，对边界截断、低样本和左右脚歧义阶段代理实施指标级评分阻断，并将事件级 Pose 覆盖不足作为 score-only 而非 feature measurement hard fail；无关异常保留为 `outside_indicator_joints` 告警，legacy 或不完整 provenance 继续 fail-closed。评分循环读取时间线每行实际版本并与 registry 精确比对，版本不匹配在落盘前拒绝。

评分闭环 v0.4.1 不改变事件、特征值或质量门禁结论；它修正可解释性聚合：即使某条记录已因特征缺失或 hard fail 不可用，`reason_codes` 仍必须同时保留身份、跳变、左右交换、目标方向、右边界截断阶段、两样本阶段和启动脚侧别歧义等并发 score-only 阻断。Bundle validator 会拒绝省略任何活动 raw flag；标准 reason code 和中文反馈则明确告诉复核者应检查哪类证据，不再退化为泛化“评分上下文未确认”。

phase v0.2 对两类边界受限证据作了保守处理：FS01 下行活动 run 抵达右边界时仅保留已观测峰，FS02 峰后只有两帧时仅保留实测减速度峰；左右脚峰接近时保留确定性的歧义侧代理。它们均有独立 provenance/quality flag，只允许 F2 特征测量并阻断依赖指标的正式 A～E；单帧尾部仍为 null。

事件候选器 v0.3.1 先修正覆盖率作用域，v0.4.0 再把候选运动参考改为“优先 body center、肩部缺失时使用直接重叠帧偏移对齐的 hip center”；这不插值、不回填特征关键点，也不是生物力学重心。v0.4.1 / phase v0.3 仅在事件右边界速度峰后的两个实测样本时间有效且严格递减时，保留该边界峰作为右截断减速阶段代理；后续样本只用于确认，不进入事件特征，且该代理继续阻断 A～E。FS02 后段特征直接锚定事件声明的 `first_step_slowdown_proxy_ms`。质量策略 v1.6.0 进一步区分特征测量与正式评分：`pose_kinematic_coverage_low` 和事件级 `primary_pose_coverage_low` 都只阻断自动边界上的 A～E，不再抹掉已经完整算出的 required feature；Track 覆盖不足、confirmed ID switch、必需 phase 和真实 feature 缺失仍硬失败。当前全片每类 34 个候选、442 条指标实例，特征层 403 条 `measured` / 39 条 `unavailable`，评分层仍为 176 条 `calibration_required` / 266 条 `unavailable`。两个同窗仍为 130/130 与 143/143 特征可测；三套产物仍是 0 grade、0 threshold。候选增减只能用人工 Event F1/Boundary MAE 判断，不能当作准确率改善。

真实 JSONL 逐条复核中，Halpe 同窗 jump/swap/target/right-censored/low-sample reason 为 34/33/10/1/2，WholeBody 同窗为 88/27/11/2/1；全片 Halpe identity/jump/swap/target/right-censored/low-sample/lead-side 为 39/193/69/32/4/1/12。泛化 `event_scoring_context_unverified` 在三套当前产物中均为 0，身份 reason 在两个无身份阻断的同窗产物中也均为 0。计数可重叠，只用于解释复核队列，不代表准确率。

为让这些阻断真正可核验，现已生成三套连续视频复核队列：Halpe 同窗 39 个去重任务/35 个唯一帧，WholeBody 同窗 80/59，Halpe 全片 261/198。任务按源帧与具体关节跨重叠事件去重，保留受影响指标、相邻帧模型坐标和完整 SHA lineage；页面可直接 seek 动态视频并导出人工 CSV。当前所有任务均为 pending candidate，人工决定和独立裁决尚未发生，因此不会自动改变本节评分状态。

候选复核不能测漏检，因此另为 Halpe26/WholeBody133 同窗各生成一套 600 帧全时间线诊断真值工作台。`pose-diagnostic-truth-pack-v1.0.0` 用 accepted 覆盖区间声明显式负样本域，用稀疏 positives 记录人工真阳性，并按 exact frame + joint/pair 协议计算 TP/FP/FN、precision、recall、F1。当前两套包均为 0 accepted coverage、0 positive、`annotation_required`，所有指标为 null；至少两名独立标注者和一名独立裁决者完成全时间线盲审之前，不能据候选数量调整 jump/swap/identity 门禁。

诊断评测也不能直接修改生产质量策略。`pose-diagnostic-quality-gate-review-v1.0.0` 要求接受标准先以外部可信登记的版本化协议冻结，且注册时间早于结果；仓库没有填入任何默认 precision/recall/F1 或样本量数值。审查时从绑定 CSV 重算全部计数和比例、按 TP/FP/FN 跨范围汇总，并拒绝报告伪报、结果后注册、范围或来源哈希不一致。当前真实报告 `reports/pose-diagnostic-quality-gate-review-v1-empty.json` 为 `annotation_and_protocol_required`；即使未来通过协议，也只具备人工策略审核资格，不会自动放宽或替换 quality v1.6。

本里程碑新增的 FS01-M03/M04、FS02-M03/M04/M05 仅测量足部上移/减速、支撑侧伸展、髋加速度、动足速度/位移和图像平面方向等 Pose 运动学代理。它们不能直接确认离地、落地、地面接触、支撑力或战术方向正确性。

## 3. 历史六项冻结基线

以下结果只用于复现最初六项闭环，不代表当前 13 项：三个冻结视频总计 15,868 帧，产生 591 个候选事件、1,182 条指标记录，其中 1,132 条 `calibration_required`、50 条 `unavailable`、0 个 grade、0 个阈值版本。报告入口为 `reports/minimum-scoring-loop-report.html`。

## 4. 历史六项真实 Pipeline 烟测

请求：`examples/request-scoring-loop-smoke.json`；输出：`runs/minimum-scoring-loop-smoke/`。

- YOLO 默认后端、RTX GPU、冻结短视频前 120 帧。
- FS01/FS02/FS09 各 1 个候选事件。
- 当时六指标：4 条 `calibration_required`，2 条 `unavailable`。
- 非空 grade 0，非空 threshold version 0。
- 同任务生成 `primary-player.jsonl`、`events.jsonl`、`features.jsonl`、`indicator-features.jsonl`、`scores.jsonl`、`event-feature-errors.json`、`scoring-loop-report.html`。

## 5. 里程碑测试记录

| 阶段 | 结果 | 说明 |
|---|---|---|
| 初始仓库 | 28/28 passed | 原有服务/视觉/评分/训练测试 |
| Pose Backend | 33/33 passed | YOLO 等价回归与 Backend 契约 |
| RTMPose runtime | 34/34 passed | Halpe26 与三候选 GPU smoke |
| 主球员 | 38/38 passed | 稳定身份和诊断 |
| 事件/特征/真值/标定 | 53/53 passed | 纯函数、误差和安全评分 |
| Pipeline 闭环 targeted | 10/10 passed | 评分闭环、服务、标定 |
| 最终全量回归 | 54/54 passed | 2026-08-13；同时完成 compileall、全部 JSON Schema 解析 |

最终 120 帧产物又通过运行校验：120 frames、383 detections、121 poses；事件/特征/指标/评分/闭环 Summary 均通过对应 Draft 2020-12 Schema，3 个事件都含事件内 Track 诊断。任何后续修改都必须重复全量测试和 120 帧 Pipeline smoke。

### 5.1 Pose 替换、视频对比与服务部署里程碑

| 验证项 | 结果 | 说明 |
|---|---|---|
| 历史 YOLO / 三档 RTMPose 三视频六指标 A/B | passed_as_unlabeled_comparison | 端到端与固定同一事件两种口径，未冒充准确率 |
| 动态对比视频 | passed | 97.03 秒完整四宫格 + 20 秒重点片段，中间帧均可解码 |
| RTMPose-M 256 在线 Worker | passed | 120 帧，FS01/02/09 各 2 段，7 calibration_required / 19 unavailable，grade 0 |
| RTMPose-M 384 分析 Worker | passed | 同一 120 帧，6 calibration_required / 20 unavailable，grade 0 |
| 双档逐帧产物校验 | passed | 每档 120 frames、383 detections、127 poses |
| 历史六项收口回归 | 80/80 passed | 当时验证部署预设、真值防泄漏、裁决、空白包安全、状态语义、真值/标定 CLI、独立测试/F4 门禁和跨产物校验；不得当作当前 13 项的独立测试证据 |
| 当前 Halpe26/WholeBody133 同窗 13 项 | passed_as_computable_no_calibration | 同一 600 帧和 `primary-player-v0.3.0` 时间线；feature 与 score 结果分开计数，见第 2 节；0 grade、0 阈值，不声称准确率 |
| 当前全量回归 | 423 tests OK（2 skipped） | registry `.14`、event v0.4.1、FS01/FS02 feature v0.4、phase v0.3、quality v1.6、scoring loop v0.4.1、测量/评分双门禁、全片模型路由、阻断/工作清单/特征缺口审计、小 ROI 实验与双人关键点真值接口、真实无 GPU 重放、诊断真值评测和不可变空标定包均纳入回归；测试通过不代表事件、特征或评分准确率 |

本次 Persistent Worker 实测（RTX 5070 Ti）Halpe26 在线档为 29.976 FPS、Halpe26 分析档为 24.900 FPS、WholeBody133 分析档为 23.240 FPS；三档均按 `.14 / quality v1.6` 注册表动态运行当前 13 项并通过 bundle 校验。短片吞吐只用于部署烟测，不是生产容量承诺；机器报告分别位于 `runs/rtmpose-m-halpe26-online-smoke/deployment-smoke.json`、`runs/rtmpose-m-halpe26-analysis-smoke/deployment-smoke.json` 与 `runs/rtmpose-m-wholebody133-analysis-smoke/deployment-smoke.json`。

## 6. 未解决问题和下一步

1. 完成人工事件边界和阶段标注，先报告 Event F1、Segment IoU、Boundary MAE，不自动晋级。
2. 完成人工校正肩/髋/膝/踝及细足参考点，输出 58 个版本化定义中实际入选特征的 MAE/P95/Bias、分视角有效率和误差预算。
3. 在查看误差结果前登记 F2→F3 接受协议，并验证特征误差显著小于潜在等级间差异。
4. 收集多教练 A～E 或排序标签，审查一致性后创建版本化阈值/序数模型资产。
5. 用独立球员、场次和机位测试集验证后才能晋级 F4。
6. Halpe26/WholeBody133 候选仍未通过 RallyMate 真值门禁；不要因官方数据集指标、关键点覆盖或本次时序代理可用性切换正式评分模型。

## 7. 真值采集包与评测防泄漏

已生成 `data/annotations/scoring-truth-pack-v1/`，当前 manifest 为 `scoring-truth-pack-v0.3.0`：覆盖 13 项，包含三个完整原视频的离线审阅页、9 个密集关键点试点事件、150 个逐帧关键点时间点、2,100 条关节标注行、51 条方向/侧别/接触代理/稳定区间/机位审计空白语义任务，以及三名教练的 234 条空白等级/排序任务。包内没有人工标签或阈值，状态为 `annotation_required`。

空白包已通过编译器，结果为：manual events 0、accepted keypoint frames 0、semantic truth 0、coach labels 0；缺失事件阶段单元 9、语义单元 30、教练指标单元 39，`video × indicator` readiness 矩阵共 39 行。三个完整视频均待审阅，随后事件/特征评测必须返回 `ground_truth_required`，教练标定必须保持 `calibration_required`。

真值实现新增三道门禁：未显式标注的模型坐标不再被保留成“真值”；同一帧同一关节的多标注者结果必须裁决后才能导入；每个视频必须覆盖 FS01/FS02/FS09 必需阶段，并按 13 个指标逐项具备同一事件、同一标签类型的多教练重叠。无法观察的语义必须显式记录 `observable=false` 和原因，不能用猜测值补齐。稀疏关键点不足以计算特征时，报告 `insufficient_keypoint_ground_truth_coverage`，不得以模型坐标补齐或显示为已评测。

## 8. 用户可见状态语义

API、任务 Summary、298 项颗粒度审计、当前 13 项报告和主 HTML 报告统一使用四种语义：`feature_status=measured` 表示事件级特征集已计算；`scoring_status=calibration_required` 表示测量与当前评分质量门禁通过，但没有经教练真值批准的 A～E 标定；`scoring_status=unavailable` 表示特征输入、必需阶段、身份连续性或评分质量门禁不足；`scored` 才表示正式等级已产生。`feature_status=measured` 与 `scoring_status=unavailable` 可以同时出现，必须保留这一差异，也不得把任何未评分状态渲染为数字 0。

历史 RTMPose-M 256 在线档 120 帧六项产物已验证为 F2 测量层结果；当前状态以 v2 注册表和第 2 节 13 项同窗 JSON 为准。13 项逐项保留事件/阶段或接触真值、特征误差、教练标定和独立测试等阻断；其他 285 项明确标记为“不在本轮最小闭环”。

## 9. 独立测试与正式评分门禁

阈值规则和序数模型契约已升级到 `1.2.0`。除绑定指标和数据集外，阈值规则必须绑定主特征版本，序数模型必须逐特征绑定单位和特征版本；运行时不匹配时拒绝加载，避免旧标定资产在特征算法变更后静默错用。生产标定资产还必须携带 `artifact_scope=production` 以及完整 `independent_test` 凭证：测试状态、是否批准正式评分、独立数据集版本、报告版本与 SHA-256、预注册验收协议版本和测试时间。`pending`、`failed` 或未批准的资产即使阈值结构合法也只能返回 `calibration_required`；`test_only` 资产只有调用者显式设置测试覆盖时才可验证评分数学，不能进入生产路径。

新增 `scripts/score_calibrated_indicators.py`，可读取任意真实运行的 `indicator-features.jsonl` 与模型版本 Summary，加载外部标定资产并输出统一 `scores.jsonl`。该命令不训练序数模型、不计算阈值。历史六项 RTMPose-M 256 烟测在零标定资产下为 10 条 `calibration_required`、2 条 `unavailable`；当前 13 项同窗结果见第 2 节，两代产物均为 0 个 grade、0 个 threshold version。

`validate_run.py` 现在不仅校验帧级视觉产物，还会在评分产物存在时强制核对完整 bundle：主球员时间线、事件与 Track 诊断、基础/扩展特征、注册表指标、Score、Summary 计数、跨文件引用、证据帧以及视频/帧 SHA-256。历史在线 256 与离线 384 六项烟测均通过；当前同窗 JSON 另记录 Halpe26 130 条与 WholeBody133 143 条指标/Score 的 bundle validation。该校验只证明契约一致，不证明关键点、事件或评分准确。

`reports/scoring-loop-completion-audit.json` 是历史六项完成度 checkpoint，不能单独作为新增五项验收证据；当前 13 项以 v2 注册表、measurement plan、真值包 manifest 和同窗比较 JSON 的一致集合为准。工程接口和安全门禁已验证，但真实 F3/F4 仍因人工事件、裁决关键点、教练标签和独立测试证据缺失而未完成。
# M30 全片 Pose 配置路由验收

当前模型路由不再依据“某模型在自己切出的事件上可测更多”做选择。对同一 2,911 帧视频，Halpe26 256×192、Halpe26 384×288 与 WholeBody133 256×192 已在 Halpe256 的同一 102 个候选事件边界上完成 13 项固定边界复算：分别为 `403/442`、`399/442`、`369/442` 条指标事件 required features 完整。

Halpe384 在各自自动边界上虽为 `415/442`，但其事件与 Halpe256 在 IoU≥0.3 时只有 `90/102` 匹配，中心边界平均绝对差 `64.26 ms`。这证明各自切分计数混入事件边界差异，不能拿来宣称更高 Pose 准确率。WholeBody133 的 133 点仍对完整手、脸、足部可视化和未来特征有价值，但没有提高当前 13 项同边界完整率。

验收决定记录在 `reports/pose-profile-routing-audit-full.json`：当前继续使用 Halpe256 作为评分主配置，384 作为分析候选，WholeBody133 作为补充拓扑；禁止逐指标、逐事件、逐特征跨模型择优拼接。该决定只基于同边界测量覆盖和可追溯性，不是精度排名，不修改 F2、grade 或 threshold。下一次模型切换必须使用人工事件边界、人工校正关键点和分视角特征误差重新评估。

# M31 评分阻断归因验收

本节记录 M31 历史合同下的 `reports/scoring-blocker-audit-halpe256-full.json`。当时 266 条 score `unavailable` 中有 227 条旧版 required features 完整、39 条涉及 feature 不完整或 hard fail；M42 已把目标方向对齐正式加入完整评分向量，因此当前计数必须以下方 M43 报告为准，不能继续把 227 当作现在时事实。

当前人工真值最高收益项是关键点跳变全时间线审阅：它参与 183 条“特征完整但正式评分阻断”的记录，其中 100 条只有这一项阻断。其次为左右交换单阻断 19 条、FS02 目标方向语义单阻断 14 条。这里的“收益”只指未来在人工证据和受控策略发布后可能恢复为 `calibration_required`，不表示诊断为误报、不表示 A～E 已可输出，也不是准确率指标。

审计器验证每个 score flag 对应的 typed reason code；目标方向不会被误写为身份连续性，跳点和左右交换也保持独立原因。审计过程不修改 quality policy、事件、特征、grade、threshold 或 maturity。

# M32 人工真值优先工作清单验收

入口：`reports/scoring-truth-priority-worklist-halpe256-full/index.html`。页面使用连续 97 秒 H.264 视频，不是单张截图；点击每个工作项的时间按钮可直接跳到候选事件或诊断时刻。

该历史工作清单精确覆盖 M31 的 227 条“旧评分向量完整但 score-only blocked”指标实例，并按 event×flag 折叠为 129 项。Pose 类 80 项全部绑定到现有诊断 queue task，0 项缺少关联；其余项路由到人工目标方向、事件边界、启动脚侧别或阶段真值。它可继续用于视频定位，但早于 M42 完整评分向量，不能作为当前阻断计数真源。

当前 `accepted_annotations=0`、状态为 `annotation_required`。清单只安排人工工作，不生成标签、不解除 gate、不修改特征、不输出 A～E；缺少教练标定时，未来完成全部复核也最多恢复为 `calibration_required`。

# M33 特征观测缺口验收

- `reports/feature-observation-gap-audit-halpe256-full.json` 必须精确绑定当前全片 `indicator-features.jsonl`、`features.jsonl`、`events.jsonl`、scoring-loop summary、registry 以及两份同候选边界模型对照 SHA-256。
- 442 条指标实例必须互斥为 403 feature measured / 39 feature unavailable；39 条必须定位到 11 个事件、178 次 required-feature 失败和 138 个唯一 `event_id × feature_name` 缺口。
- 每个 compact invalid feature 必须与源 feature 的 name/version/value/unit/confidence/valid/reason/source frames 完全一致；任何跨层篡改都不得通过审计。
- 根因必须保留真实 reason：134 个事件内有效 Pose 覆盖不足、2 个双脚上抬代理不可观察、1 个 variability 样本不足、1 个速度/角速度样本不足。不得把不可观察改写成数值 0。
- 替代模型只允许在完全相同候选事件边界下报告可观测性：Halpe384 对当前缺口 2 gain / 6 regress，WholeBody133 为 0 / 34；`accuracy_claim=false`、automatic profile fallback=false。
- 审计不得改变 measurement/scoring gate，不得生成 grade/threshold，不得宣称 F3/F4；所有恢复动作必须经过人工事件/关键点审阅与重新计算。
- 全量回归必须为 411 tests OK（2 skipped），新审计实例必须通过 Draft 2020-12 Schema 校验。

# M34 小 ROI Pose 恢复实验验收

- 生产 `PoseEstimator` 默认最小 ROI 必须继续为 32px；8px 只能由独立实验参数显式启用，Service/Pipeline 不得自动 fallback。
- 实验必须精确绑定同一视频、frames/detections、`primary-player.jsonl`、Pose 权重/config、registry、M33 gap audit 和 102 个固定候选事件的 SHA-256；除 `min_roi_size_px` 外不得更换模型或事件边界。
- 仅允许重推当前 `max_players=2` 调度内、基线因 32px 尺寸保护跳过的 131 帧；结果必须如实报告 131 attempted / 131 pose output，而不能称为 131 帧人工准确。
- 固定边界 442 条指标实例必须为 403 measured→measured、20 unavailable→measured、19 unavailable→unavailable、0 regression；实验最多证明 feature observability 可从 403/442 提升到 423/442。
- 13 秒动态 A/B 必须为 H.264 1920×720，首/中/末帧可解码，逐帧显示源时间、候选事件、32px/8px、有效关键点和“not truth/A～E”警告；主报告链接不得丢失。
- `accuracy_claim`、`ground_truth_provided`、`production_enabled`、自动 fallback、measurement/scoring gate 修改、grade、threshold 和 maturity promotion 必须全部为 false。正式启用前必须有按小框尺寸和视角分组的人工关键点误差及独立测试。
- M34 实验实例必须通过 Draft 2020-12 Schema；相关渲染/报告回归纳入全仓 417 tests OK（2 skipped）。

# M35 小 ROI 关键点真值与误差接口验收

- 任务集必须覆盖 M34 全部 131 个恢复帧和每帧 14 个评分相关关节，共 1,834 项；manifest 必须绑定原视频、实验 frames/report、主球员 timeline、gap audit 与比较视频 SHA-256，并冻结 task canonical hash。
- 标注 UI 只能显示原视频、检测框放大和人工点；不得嵌入、预填或以隐藏字段携带模型关键点坐标。比较视频只能在完成裁决后作为旁证打开，不得作为真值来源。
- 每个任务必须有至少两名唯一 annotator 的独立原始意见；accepted adjudication 必须引用这些 source annotation ID，并由不属于上述 annotator 集合的 reviewer 作出。身份冲突、任务越界、重复或哈希漂移必须 fail closed。
- 人工真值完整前，error report 必须为 `annotation_required`，metrics/per-joint/per-bbox-stratum 为 null，details 为空；不得因部分标注改变运行时或启用 8px。
- 完整裁决后只允许报告像素 MAE/P95、bbox 长边归一化 MAE/P95、x/y Bias、预测有效率和分组结果。PCK 需外部预注册阈值；路由切换还需独立视频和外部接受协议，不能由当前结果自行产生阈值。
- 当前真实包为 0 raw annotation、0 accepted adjudication、0 accuracy claim，生产默认仍为 32px，13 项仍为 F2，grade/threshold/maturity 均未改变。评测器还会拒绝任何编译后改动但未重新编译的 CSV。三份 Schema 实例通过 Draft 2020-12 校验；全仓回归为 423 tests OK（2 skipped）。

# M36 13 项可计算性与 residual fail-closed 验收

- `reports/residual-indicator-computability-small-roi-v1.json` 必须精确绑定实验 fixed-boundary comparison、registry、实验 frames 和主球员 timeline 的 SHA-256，并按 registry required feature 验证单位和特征版本。
- 13/13 个 registry 指标必须至少有一个真实 measured 实例；每个实例的全部 required feature 必须 value 非 null、`valid=true`、confidence 存在、`source_frames` 非空。当前实际为每项 30～34 条，总计 423/442 measured。
- 剩余 19 条 unavailable 必须完整归因且总数闭合：12 条画面边界裁切、6 条 source Track 过渡、1 条视频起始边界截断，集中在 6 个候选事件。任何未分类 residual 都不能被报告为已解决。
- `reports/pose-scoring-ab/residual-computability-evidence-browser.mp4` 必须逐帧显示源视频、主球员框/骨架、Track、有效点数、分类和受影响指标数；H.264 首/中/末帧必须可解码。视频不能把候选边界称为真值。
- `zero_fill_used`、quality gate 修改、跨模型挑值、production route 变更、grade、threshold 和 maturity promotion 必须全部为 false。画面外关节、未确认身份过渡和缺失前置上下文必须继续输出 `unavailable`。
- “13/13 可计算”只表示所有指标在至少一个合格真实候选实例上完成特征合同，不表示每个事件都必须有值，也不等于 Event F1、关键点/特征准确率、F3/F4 或 A～E 评分能力。
- M36 Schema 定义和真实实例必须通过 Draft 2020-12；主报告本地引用不得缺失，HTML/MP4/审计 JSON/盲标工作台必须通过本地 HTTP；当前全仓回归为 428 tests OK（2 skipped）。

# M38 每次上传逐指标计算就绪验收

- Pipeline 必须从本次运行实际 registry、events、indicator-features 与 scores 构建 `calculation-readiness.json`，四类来源都记录路径和大写 SHA-256；缺少来源、哈希格式错误、指标成员或 event/score 关联不一致必须拒绝。
- 每项必须保留 required events/features、候选/measured/unavailable 数量、score 状态、特征失败、measurement hard flags、scoring-only flags 和 measured 证据帧。`measured_on_at_least_one_candidate` 只允许在至少一条完整 feature record 且 source frames 非空时出现。
- 短烟测必须如实为 11/13，而不是因为注册表有 13 项就宣称 13 项都已计算；缺失项为 FS01-M03 和 FS02-M05。全片必须从同一契约重算为 13/13、403/442 measured、39/442 unavailable。
- 报告必须固定 `candidate_events_are_ground_truth=false`、`formal_scoring_ready=false`，所有 accuracy/truth/grade/threshold/maturity 声明均为 false，所有 remediation 都必须禁止自动放宽质量门禁。
- Stage-1 Summary Schema 不得再硬编码六项；真实 `target_indicator_count=13` 的 M38 Summary 与两份 calculation-readiness 实例必须通过 Draft 2020-12，运行 bundle必须继续通过 `validate_run.py`。
- 当前验证结果：440 tests OK（3 skipped）；主报告 54 个去重本地引用 0 缺失，主页面、短烟测计算报告和全片计算报告均可由本地 HTTP 打开。

# M39 每视频代表性测量组合验收

- 每个新 Pipeline 运行必须生成 `indicator-measurement-portfolio.json`；成员集合精确等于运行时 registry。每项最多一个代表测量，且只允许来自 `feature_status=measured`、全部 required feature 有效且证据帧非空的同一事件实例。
- 选择不得读取 feature value、grade、threshold 或运动表现，不得跨模型拼接。当前策略只使用 required-feature 最低/平均置信度、事件置信度和质量状态，并用事件时间与 ID 保证确定性。
- 短烟测必须如实输出 11/13 个代表测量，FS01-M03 与 FS02-M05 保持 unavailable；97 秒全片必须输出 13/13 个代表测量，并保持 403/442 个 measured 实例的原始统计。缺项不得用 0、插值或较弱质量门禁补齐。
- 每条代表测量必须追溯到 video、person Track、event、required-feature 版本、单位、置信度与 source frames；`grade` 和 `threshold_version` 在当前 F2 状态必须为 null。
- `validate_run.py` 必须对声明的计算就绪报告和代表性组合验证来源 SHA-256；篡改派生报告的任一源哈希必须使 bundle 校验失败。Service API 和主 HTML 报告必须能下载/展示两个机器报告。
- 当前验证结果：445 tests OK（4 skipped）；主报告 57 个去重本地引用、缺失 0，主页面、M39 Worker 报告和短片/全片 portfolio 均可由本地 HTTP 打开。

# M40 同一次动作周期 13 项闭合验收

- 周期只能由同一 video、person Track 和 detector source 的 FS01、FS02、FS09 构成；必须同时满足 FS01 initiation=FS02 start、FS02 peak=FS09 peak、FS09 start 位于 FS02 内且 FS09 end 覆盖 FS02 end。不得依赖事件 ID 文本序号猜测归属。
- 每个闭合周期必须精确包含 registry 的 13 项：FS01 四项、FS02 四项、FS09 五项。代表周期只能从这一组事件取特征，`cross_cycle_feature_mixing_used=false`；任何 required-feature 缺失都必须留在该周期的 unavailable 集合。
- 全片必须闭合 34 个周期、unmatched event=0，其中 25 个周期为同周期 13/13 measured；120 帧真实 Worker 必须如实为 2 个周期、0 个完整周期、最佳 11/13，不得借另一周期补齐。
- 周期选择只允许使用指标完整数、评分上下文门禁通过数、required-feature 置信度和事件置信度，不得使用 feature value、运动表现、grade 或阈值。全片代表周期必须为 13/13 特征完整、12/13 评分上下文通过；所有 grade/threshold 继续为空，候选周期不得宣称事件准确率。
- `scoring-cycle-measurement.json` 必须由 Pipeline 原生生成并出现在 Summary、Service 白名单和 HTML；Schema、Python validator 与 `validate_run.py` 必须拒绝阶段关系、required-feature 合同或来源 SHA-256 篡改。
- 当前验证结果：451 tests OK（5 skipped）；主报告 60 个去重本地引用、缺失 0，主页面、M40 Worker 报告及短片/全片周期 JSON 均可由本地 HTTP 打开。

# M41 FS02-M02 目标方向参考上下文验收

- 目标方向必须由 `scoring-reference-context-v1.0.0` 显式提供；契约精确绑定视频 SHA、FS02 事件和边界。不得从 `launch_direction_deg` 或其他人体轨迹反推战术目标。
- accepted 记录必须包含 image-plane `target_direction_deg`、置信度、观察者和独立 reviewer；pending/unobservable 必须保留 null 并给出原因。自审、accepted 旧边界/重复事件、视频不匹配和 court-plane 无变换输入必须拒绝；无评分作用的 stale pending 只能被忽略并重新生成，不能解除门禁。
- 只在参考方向和 `launch_direction_deg` 都有效时计算 `target_direction_alignment_error_deg` 并解除 FS02-M02 的目标方向 score-only gate；这不改变 F2、不会产生 A～E 或 threshold。
- `indicator-features.jsonl` 与 `scores.jsonl` 必须保存完全一致的 `scoring_context`，score evidence 必须包含同一对象；validator 必须拒绝用 pending/missing 上下文移除目标方向阻断。
- 真实全片工作清单为 34 pending / 0 accepted，回放状态 `provided_without_available_reference`，因此 FS02-M02 继续 unavailable。合成 accepted 测试只证明契约和角度计算可执行，不是实际教练真值或效果结论。
- 实际 GPU Worker 120 帧运行以稳定视频 ID 消费参考文件；基于当前事件生成的 3 条 pending 记录在第二次 Worker 运行中 3/3 精确应用，仍为 available=0、grade=0、threshold=0，跨产物校验通过。
- M41 最终回归为 458 tests OK（6 skipped）。当前 Worker 包与 97 秒全片包均经独立 `validate_run.py` 验证；新增上下文/请求 Schema、主报告 65 个本地引用和 4 个本地 HTTP 入口全部通过终检。

# M42 测量向量 / 完整评分向量验收

- 权威 registry 为 `pose-wave-2026-08-22.17`。`required_features` 是完整标定/评分向量；可选 `measurement_features` 是 Pose/传感器独立可测子集。除 FS02-M02 外，两者默认相同。
- FS02-M02 的 Pose 测量向量有 4 项；完整评分向量按序追加 `target_direction_alignment_error_deg`。该特征版本为 `target-direction-alignment-v1.0.0`、单位 deg，0 表示真实 0° 对齐，缺失必须为 null。
- 运行时、人工事件特征、标定编译、独立测试、batch 重评分、Bundle 校验和报告均消费同一完整评分向量。删除 context-only 特征、伪造 `scoring_feature_status`、将 pending target 当 0 或只用 4 项向量重评分都会被拒绝。
- 实际 GPU Pipeline：120 帧、9 个事件、39 条指标实例；36/39 Pose 测量完整，13/13 指标至少有一个 measured 候选，3 个周期中 1 个同周期达到 13/13 Pose 测量，最佳评分上下文 8/13。3 个 FS02 target 均 pending，故 FS02-M02 完整评分向量 unavailable。
- 97 秒全片：2,911 帧、102 个事件、442 条指标实例；403/442 Pose 测量完整，评分状态 176 calibration_required / 266 unavailable。34 个 FS02 target 均 pending。两套产物 grade=0、threshold=0，并通过 `validate_run.py`。
- 真值包重新编译后仍为人工事件 0、accepted keypoint 0、semantic 0、coach label 0；新不可变数据集 `rallymate-calibration-f0179d4e3dc40cb6` 为 annotation_required、samples=0、13 个 prepared 文件、F3=false、无阈值/模型/晋级资产。
- M42 不证明目标方向、事件或 Pose 准确；下一科学门禁是人工 target/事件/关键点与分视角误差评测，而不是经验生成 A～E。

# M43 类型化阻断归因与目标方向误差预算验收

- `reports/scoring-blocker-audit-halpe256-full-m43.json` 绑定当前 M42 全片 scores/summary SHA。442 条记录互斥为：176 条完整评分向量且只缺标定、194 条完整评分向量但 score-only blocked、3 条评分向量不完整且无 score-only block、66 条评分向量不完整且同时 blocked、3 条 hard fail；总计 176 calibration_required / 266 unavailable。完整评分向量缺失包含 34 条 FS02-M02 人工目标方向缺失，不得误称 Pose 特征失败。
- 审计器同时检查类型化原因“少报”和“多报”。当前 `event_identity_continuity_unverified` 精确对应 43 条 source Track/身份 flag；跳点 201 条、左右交换 69 条、目标方向 34 条、事件运动学覆盖 44 条、主 Pose 覆盖 22 条、启动脚侧别 18 条均保持独立原因，支持记录数与 reason 记录数逐项相等。
- `evaluate_scoring_truth.py` 现在接收 `--manual-semantics`，并把 `target_direction_alignment_error_deg` 纳入 MAE、P95、Bias、有效率、分视角与 Pose/事件边界/平滑/缺失四类误差预算。人工 target 必须为 accepted image-plane 语义且 observer/reviewer 独立；缺失时 value 与误差均为 null，不得用 0。
- 当前 `reports/truth-pack-empty-evaluation.json` 仍为 `ground_truth_required`：required context feature 为 `target_direction_alignment_error_deg`，人工 semantic 记录数 0，`context_feature_truth_complete=false`、`feature_truth_complete=false`。因此成熟度继续为 F2，grade/threshold 均为 0。
- M43 全仓回归为 464 tests OK（6 skipped）；M42 全片与实际 GPU Worker bundle 均通过 `validate_run.py`。主报告 63 个去重本地引用、缺失 0；主页、M43 blocker audit、空真值评测与人工工作台均由本地 HTTP 返回 200。

# M44 当前全量真值行动清单验收

- `reports/scoring-truth-action-worklist-halpe256-full-m44/worklist.json` 必须绑定 M42 scores/events/summary、M43 blocker audit、registry `.15`、当前 M42 Pose 诊断队列、目标方向文件和连续视频 SHA-256；任一来源漂移都拒绝生成。
- 442 条指标实例中 176 calibration_required 不进入“故障修复”任务；266 条 unavailable 必须 266/266 至少分配一个人工动作。当前去重为 168 个 `event×truth requirement` 工作项、481 条实例×动作关联，不允许通过只计算 work item 数掩盖多重阻断。
- 工作类型必须分离：92 个 Pose 诊断、11 个人工关键点/特征、34 个目标方向、31 个事件/阶段/其他语义。目标方向缺失属于 scoring context，不得写成 Pose 特征失败；全部 Pose 工作项必须关联当前诊断 queue，当前 missing=0。
- JSON Schema、Python validator、CSV 和连续视频 HTML 必须保持 `annotation_required`，accepted=0，grade/threshold/maturity/quality/scoring state 均不修改；覆盖率不是准确率，完成单个动作不自动解除门禁。
- M44 全仓回归为 470 tests OK（7 skipped）；当前全片 bundle 再次通过 `validate_run.py`，真实 worklist 通过 Draft 2020-12 Schema。主报告/行动清单/当前 Pose 队列分别有 64/6/1 个去重本地引用且缺失 0，四个 HTTP 入口均返回 200。

# M45 行动证据完成度状态机验收

- `reports/scoring-truth-action-readiness-halpe256-full-m45.json` 必须绑定 M44 worklist、compiled truth validation、人工事件/关键点/语义、事件与特征误差评测、当前全片 Pose truth manifest/evaluation 和 FS02 目标方向文件；所有来源 SHA-256 必须一致。
- 每个工作项只允许 `evidence_satisfied`、`review_in_progress_not_adjudicated`、`annotation_required`。状态必须从原始证据重算；浏览器复核完成但未独立裁决不得冒充真值，覆盖率不得冒充准确率。
- Pose 证据必须有对应诊断类型的完整时间线 accepted coverage；身份连续性同时要求 identity ambiguity 与 source Track switch 两类覆盖。事件/阶段使用一对一全局匹配，IoU 0.5 仅是事件关联协议。特征项必须由人工事件/关键点重算并精确命中当前无效特征；目标方向必须绑定当前事件并 accepted。
- 当前全片 Pose truth pack 为 2,911 帧、261 个候选任务，accepted coverage/positive 均为 0。M45 报告因此保持 `annotation_required`：168/168 工作项、266/266 指标实例未满足，481 条实例×动作关联完整保留。
- readiness 完成不直接修改 quality gate、score、grade、threshold 或 maturity；完成证据后仍须显式重跑评分和标定，且无标定时最高为 `calibration_required`。全仓回归为 477 tests OK（8 skipped）；真实 M45 报告通过 Python validator 与 Draft 2020-12 Schema，当前 2,911 帧 bundle 通过 `validate_run.py`。主报告、M44 清单和全片 Pose 真值页的 67/6/1 个本地引用缺失 0，四个 HTTP 入口均返回 200。

# M46 共享证据获取计划验收

- `reports/scoring-truth-evidence-plan-halpe256-full-m46/plan.json` 必须绑定并重放当前 M45 readiness 与 M44 worklist；168 个 work item 和 266 个 unavailable 指标实例必须全部出现在至少一个证据单元依赖中。
- 当前必须折叠为 87 个证据单元：4 个完整时间线 Pose、21 个事件边界、9 个阶段、2 个事件族密集关键点、11 个特征真值、6 个侧别语义、34 个目标方向。折叠只去重共同证据，不允许删除任一原 work item 条件。
- 完整时间线 `keypoint_jump / left_right_swap / primary_identity_ambiguity / source_track_switch` 必须分别保留；身份连续性依赖后两者同时完成。当前四单元分别关联 54/28/10/10 个 work item，不能按共享数量伪装成人工真值已经存在。
- JSON Schema、结构 validator 与 source replay validator 均须通过；同步伪造状态、计数、优先级和依赖仍必须被 source replay 拒绝。HTML/CSV 只用于视频定位和任务分派。
- 当前 87/87 为 `annotation_required`。优先级不是准确率、运动质量或工时估计；完成后仍需显式重跑 M45/评分/标定，无教练标定时最高为 `calibration_required`。全仓回归为 484 tests OK（9 skipped）；真实计划通过 Draft 2020-12 Schema 与 source replay。主报告/M44 清单/全片 Pose 真值/M46 计划的 68/6/1/3 个本地引用缺失 0，五个 HTTP 入口均返回 200。

# M47 单命令不可变真值刷新验收

- `refresh_scoring_truth_evidence.py` 必须先在临时副本预检人工 CSV；无错误后才允许编译 canonical truth，并将 compiled truth、Pose truth、registry、worklist 与目标方向复制到唯一 refresh 目录。
- 事件/特征评测、Pose 诊断评测、M45 和 M46 必须全部引用该稳定快照或 hash-bound 模型产物。成功 manifest 必须最后写入；`latest.json` 只能在强校验全部通过后通过原子替换更新。失败预检不得创建运行目录或 latest。
- 强校验必须复算全部 artifact/source SHA，从快照重新执行 M45，并 source-replay M46。修改 manifest SHA、readiness、状态、计数、优先级或依赖均不得通过。
- 当前 `reports/scoring-truth-refresh/m47-empty-v2/` 为完整成功快照，并将原始 Pose coverage/positives CSV 单独写入 source fingerprint；真值仍为空：manual event/keypoint frame/semantic/coach label 为 0/0/0/0；work item、指标实例、证据单元满足数为 0/168、0/266、0/87。所有安全字段禁止 grade、threshold、quality/scoring state 变更和成熟度晋级。
- 总报告必须通过 `reports/scoring-truth-refresh/latest.json` 读取并显示当前 refresh ID 与状态；过期或 SHA 不一致的 latest 必须 fail closed。全仓回归为 490 tests OK（10 skipped）；真实 manifest 通过 Draft 2020-12 Schema、18 个 artifact SHA、source fingerprint 与强 source replay，且无失败 marker。当前全片 bundle 通过跨产物校验；主报告/M47 首页/M47 共享计划/M44 清单/全片 Pose 真值页的 69/5/3/6/1 个本地引用缺失 0，最新四个 HTTP 入口均为 200。

# M48 真值到标定数据集不可变交接验收

- `build_scoring_truth_calibration_handoff.py` 必须只读取已通过强校验的 M47 latest；registry、人工事件、语义、教练标签、truth validation/manifest 必须复制为交接快照，frames 与 primary timeline 必须精确复核 M47 SHA。
- manual-event feature build 只能使用 accepted manual event 的原 event_id、边界、phase 和 Track；禁止调用候选事件检测器、IoU 模糊关联或把候选事件提升为真值。其他视频没有绑定测量源时必须明确阻断。
- calibration dataset 必须只消费 manual-boundary indicator features，并为当前注册表精确生成 13 份 prepared 文件。每份必须先通过非拟合结构校验，再单独执行 fit-ready 门禁；本里程碑不得运行拟合器、写 candidate、阈值、模型或等级。
- 当前 `m48-m47-empty-v2` 为 `annotation_required`：accepted event/manual feature/sample 为 0/0/0，13/13 prepared 结构有效，0/13 fit-ready，所有 grade/threshold/model/promotion 安全字段为 false。
- validator 必须重放 M47、来源 SHA、filtered events、manual feature lineage、dataset sources、prepared 与 fit readiness。真实 manifest 和 Schema 均通过；全仓回归为 497 tests OK（11 skipped），主报告/M48 首页本地引用 121/4、缺失 0，主报告/M48/latest 三个 HTTP 入口均为 200。

# M49 三视频测量源与统一标定组合验收

- measurement source manifest 必须与 truth manifest 的 3 个 video ID 精确相等，不得漏视频或额外混入视频；真实来源为 1,441 / 2,911 / 11,516 帧，共 15,868 帧。
- 三段必须使用同一 RTMPose-M Halpe26 256×192 模型 SHA、配置、原生拓扑，并各自生成与 frame index/timestamp 精确对齐的 `primary-player-v0.3.0` timeline。既有 Pose 帧复用时必须显式记录 `gpu_inference_executed=false`。
- portfolio 必须同时验证 M47 truth refresh 与 measurement source latest 使用同一 truth manifest 和 registry。每段仅接受人工 event ID/boundary/phase/Track 重算特征；禁止候选检测、IoU 近似关联、跨视频借帧或跨模型拼接。
- calibration compiler 必须同时消费三段逐视频 manual-boundary indicator features，并精确生成当前 13 项 prepared dataset。当前空真值为 event/feature/sample=0/0/0、13/13 prepared 结构有效、0/13 fit-ready。
- 强 validator 必须重放视频 SHA、Pose/timeline contract、source snapshots、逐视频 manual-feature lineage、dataset source/output、prepared、fit readiness、状态、计数和 source fingerprint。真实组合不得生成 candidate、阈值、模型、grade 或 F3/F4。
- M49 定向测试必须 9/9 通过；当前全仓为 506 tests OK（13 skipped optional jsonschema）。两份真实 manifest 通过系统 Python Draft 2020-12 Schema；主报告/M49 页面本地引用为 69/3、缺失 0，主页、M49、portfolio latest、measurement-source latest 四个 HTTP 入口均为 200。

# M50 三视频 13 项计算覆盖验收

- 输入视频集合必须与 M49 measurement source manifest 精确一致：3 段、15,868 帧；每段必须绑定当前 registry、同一 RTMPose-M Halpe26 模型 SHA/拓扑和各自 `primary-player-v0.3.0` timeline。M50 复用既有 Pose，`gpu_inference_executed=false`。
- 三段运行包必须各自通过 `validate_run_artifacts`；coverage validator 必须重放 report/summary/events/indicator-features/scores、来源 SHA、event×indicator×feature 精确关联和 aggregate count。缺任一视频、伪造 feature/score、模型或 timeline 漂移必须拒绝。
- 当前机器结果必须为 546 个候选事件、2,366 条指标实例、2,276 条 feature measured / 90 条 feature unavailable；13/13 指标在 3/3 视频中各至少有一条完整 measured vector。代表证据只能按最早事件确定，不得按特征值或运动表现挑选。
- 评分口径必须与测量口径分离：828 条 `calibration_required`、1,538 条 `unavailable`，quality gate 为 409 pass / 1,939 advisory / 18 hard fail；grade=0、threshold=0。
- `candidate_events_are_ground_truth`、event/feature accuracy、formal score、自动 F3/F4 和“measurement success 等于 accuracy”必须全部为 false。该里程碑不能替代人工 Event F1/Segment IoU/Boundary MAE、人工关键点特征误差、多教练标定或封存独立测试。
- M50 定向与动态报告回归必须 25/25 通过；当前全仓为 512 tests OK（14 skipped optional jsonschema）。真实 coverage 必须通过 Python 强校验和系统 Python Draft 2020-12 Schema（0 errors）；主报告/M50 页面本地引用 70/0、缺失 0，主页、M50 页面和 latest JSON 三个 HTTP 入口均为 200。

# M51 计算完整性与评分就绪互斥拆解验收

- 审计必须逐条消费并区分 `features`、`scoring_features`、`measurement_allowed`、`scoring_allowed` 和最终 score status；不得以 `feature_status` 或 `unavailable` 一个字段推断全部原因。
- 2,366 条实例的正交计数必须为：原始 measurement vector 完整/不完整 2,282/84，measurement gate 允许/hard fail 2,348/18，运行时 feature measured/unavailable 2,276/90，完整 scoring vector 2,102，scoring gate allowed 839，同时具备完整评分向量和允许评分证据 828。
- 最先阻断层必须互斥且穷尽：measurement hard fail 18、measurement vector incomplete 72、scoring context incomplete 180、scoring evidence blocked 1,268、calibration-only missing 828；五类合计 2,366，前四类合计必须等于原始 1,538 unavailable，最后一类必须等于 828 calibration_required。
- validator 必须从 M50 coverage 和三套 source bundle 重放所有计数、原因、flag、组合与 SHA。保持总数不变但在分类间移动记录必须被拒绝；缺失值补 0、解除 measurement/scoring gate、准确率声明、grade/threshold 或成熟度晋级必须 fail closed。
- “ready for calibration application” 只表示当前完整向量通过现有评分证据门禁，不能解释为事件/特征准确或已可输出 A～E。候选事件、关键点诊断、教练标定和独立测试仍需真实证据。
- M51 与动态报告定向回归必须 26/26 通过；当前全仓为 519 tests OK（15 skipped optional jsonschema）。真实 audit 必须通过 Python 强 source replay 和系统 Python Draft 2020-12 Schema（0 errors）；主报告/M51 页面本地引用 71/0、缺失 0，主页、M51 页面和 latest JSON 均为 HTTP 200。

# M52 FS09-M02 未观察到净制动的可测负证据验收

- `braking_side_code` 必须优先使用正的 event-edge 踝速度下降；仅当左右净下降都不为正且至少一侧存在严格为正的局部减速峰时，才允许以较强局部峰确定候选侧。该规则是特征定义，不是 A～E 阈值。
- 被选侧的 `braking_ankle_speed_drop_body_s` 必须保留实测负值或 0，禁止改写为 0 分、缺失或伪造的正减速；`braking_ankle_slowdown_to_hip_deceleration_ms` 必须继续使用真实 `timestamp_ms` 峰值。局部峰、输入或边界样本不足时仍须 null/unavailable。
- 输出 provenance 必须包含左右净速度下降、左右局部减速峰、峰时间、选择原因和 `not_ground_contact_or_force` 语义；禁止称为真实制动脚触地、承重或地面反力。
- 三视频真实重放必须保持 546 个候选事件、2,366 条指标实例和完全相同的 quality gate 计数。当前 feature measured/unavailable 为 2,290/76；评分为 833 calibration_required / 1,533 unavailable，grade=0、threshold=0。
- M51→M52 的 14 条恢复必须全部来自“输入完整且局部减速峰存在”的 FS09-M02；真正缺样本的记录不得恢复。评分证据门禁独立生效，所以只允许 5 条进入 calibration_required，其余 9 条继续 unavailable。
- M52 收口必须通过全仓 520 项测试（15 项 optional jsonschema skipped）、三套真实 run bundle 强校验以及 coverage/readiness/requirements/truth refresh/handoff 的 Draft 2020-12 Schema；当前主报告与四个 M52 工作页共 138 个本地引用、缺失 0。

# M53 FS02-M03 未观察到正向膝伸展的可测负证据验收

- `support_knee_extension_velocity_deg_s` 必须继续优先使用正向峰值伸展；仅当左右膝都完整可见、两侧峰值都不为正且不存在精确平局时，才允许以数值较大的非正峰作为测量候选。该规则是可观测性语义，不是 A～E 阈值。
- `drive_side_code` 在无正向伸展时必须保持 0；测量候选侧不得冒充真实支撑脚。输出速度必须保留实际负值或 0，时序必须继续使用 `timestamp_ms`，禁止取绝对值、补 0 或声称触地、承重、发力、功率。
- 缺任一膝输入、精确平局或必要时点缺失时仍须 null/unavailable。provenance 必须包含候选侧、选择原因和 `nonpositive_candidate_does_not_assert_support_or_force=true`。
- 三视频真实重放必须保持 546 个候选事件、2,366 条指标实例和 quality gate 409 pass / 1,939 advisory / 18 hard fail。raw measurement vector 完整/不完整为 2,297/69，运行时 feature measured/unavailable 为 2,291/75；评分为 834 calibration_required / 1,532 unavailable，grade=0、threshold=0。
- 互斥就绪拆解必须为 18 measurement hard fail、57 measurement vector incomplete、180 scoring context incomplete、1,277 scoring evidence blocked、834 calibration-only missing；五类合计 2,366，前四类合计 1,532。
- 当前 registry 必须为 `pose-wave-2026-08-22.17`，FS01/FS02 feature contract 为 `fs01-fs02-pose-proxies-v0.5.0`。真值包、measurement source、coverage/readiness、refresh、handoff、三视频 portfolio、requirements 和主报告必须绑定同一版本与 SHA；人工真值仍为 0 时不得生成候选标定资产、阈值、等级或 F3/F4。
- M53 收口必须通过全仓 521 项测试（15 项 optional jsonschema skipped）及三套真实 run bundle 强校验。主报告 129 个本地引用必须全部存在，主页、coverage、readiness 和 portfolio 四个 HTTP 入口必须返回 200。

# M54 平滑反事实可重建性验收

- FS09 v0.2 的无平滑反事实必须复用生产 `braking_side_from_speed_drops` 纯函数：正净速度下降优先；双侧净下降均不为正时，从 raw 踝速度按实际 `timestamp_ms` 求局部减速峰。不得继续使用只看净下降的旧侧别逻辑。
- `braking_side_code=0` 是“完整观测下双侧/无法区分”的合法诊断码，不是缺失。所选侧无法确定时 `braking_ankle_speed_drop_body_s` 仍须 unavailable；不得用任意一侧或第一个 dict 序列代替。
- 覆盖审计必须精确绑定当前 registry 版本、registry SHA 和三份 `features.jsonl` SHA，只统计 registry required event code 下的 51 个 Pose 特征；唯一非 Pose 上下文特征 `target_direction_alignment_error_deg` 必须显式排除。
- 当前三视频 10,192 条 required Pose feature 记录中，9,971 条为有效数值；5,695 条可以从序列化 raw evidence 无歧义重放无平滑对照，覆盖率 57.115635%。51 个特征中 27 个全量可重放、24 个尚不可重建、0 个未观察到。
- `braking_side_code` 与 `braking_ankle_speed_drop_body_s` 必须分别达到 178/178 可重放；两个派生峰值时序差在 payload 不足时继续以 `derived_timing_series_counterfactual_not_reconstructable` 返回 null，不得猜测。
- 审计必须声明 `ground_truth_provided=false`、`accuracy_claim=false`、`counterfactual_coverage_is_feature_accuracy=false`，且不得修改 quality gate、成熟度、grade 或 threshold。单因素 raw/smoothed 差异不是可加和 Shapley 误差预算。
- M54 收口必须通过 Python 强 validator、Draft 2020-12 Schema（0 errors）、51 项相关回归和全仓 527 项测试（15 项 optional jsonschema skipped）。

# M55 FS02-M03 平滑反事实精确回放验收

- `drive_side_code` 与 `support_knee_extension_velocity_deg_s` 的 raw/no-extra-smoothing 对照必须从左右膝屈曲序列按实际 `timestamp_ms` 计算负导数峰，并复用生产侧别与测量侧纯函数；不得复制一套近似选择规则。
- `support_drive_to_moving_foot_rise_proxy_ms` 必须使用 raw 膝伸展峰与 raw 移动脚垂直速度峰的真实时间戳。缺峰、缺侧或非有限输入须返回 null/reason，禁止固定 FPS、补 0 或猜测第一条序列。
- `hip_acceleration_along_launch_direction_body_s2` 在缺少 raw 启动方向原语时必须保持不可重建；生产平滑方向不能冒充无平滑反事实。
- 当前三视频的上述三项各为 179/179 可重放；总覆盖为 6,232/9,971（62.501254%），30/51 特征全量可重放、21/51 不可重建。覆盖率只表示 payload 可重放性。
- v1.1 报告必须逐特征输出 comparison kind、computed count、code agreement 或数值 mean/P95/signed/max difference，并绑定最大差异事件证据。任何影响量都必须声明为平滑敏感性，而非人工真值误差、准确率或等级区分度。
- 当前 drive-side agreement 为 144/179；膝伸展速度 mean/P95 absolute difference 为 98.28672910/321.77548943 deg/s；时差 mean/P95 为 229.73743017/963.10 ms。这些数值不得生成 A～E 阈值、grade 或 F3/F4 晋级。
- M55 必须通过 34 项定向回归、530 项全仓测试（15 项 optional jsonschema skipped）、当前/历史报告 Draft 2020-12 Schema 和 M55 完整来源重放。

# M56 FS02-M02 启动方向平滑反事实验收

- `launch_direction_deg` 的 raw/no-extra-smoothing 值必须从序列化 raw `hip_position[x,y]`、实际 `timestamp_ms` 和完整事件索引调用生产 `launch_direction_from_hip_motion`；禁止仅用首尾两点、固定 FPS 或另一套方向公式近似。
- raw 向量必须逐样本为二维；维数错误、时间戳不递增、分量不可用或合成方向不确定时保持 null/reason。不得用 0、smoothed summary 或第一条 dict 序列补齐。
- 启动/运动方向差必须使用跨 ±180° 的最短环形差。−179° 与 179° 的绝对差应为 2°，不得报告为 358°；机器报告须显式标记 `comparison_kind=circular_difference_deg`。
- 当前 `launch_direction_deg` 必须达到 182/182 可重放；总覆盖为 6,414/9,971（64.326547%），31/51 特征全量可重放、20/51 不可重建。
- 当前 mean/P95 absolute circular difference 为 4.57920293°/10.74166982°，最大值 160.51513435°并绑定 video/event。它们是平滑敏感性，不是人工真值 MAE、准确率、动作等级或模型比较结论。
- `target_direction_alignment_error_deg` 可在未来 accepted 人工 target 下复用 raw launch direction 形成 smoothing budget；当前人工目标方向为空时仍须 unavailable。`hip_acceleration_along_launch_direction_body_s2` 和语义不可信的旧 stability raw payload继续 fail closed。
- M56 必须通过 46 项联合定向回归、533 项全仓测试（15 项 optional jsonschema skipped）、三代报告 Draft 2020-12 Schema、M56 source replay 和动态报告引用校验；不得修改 F2、grade、threshold 或 quality gate。

# M57 FS09 稳定持续时间证据合同验收

- `stability_duration_ms` 的生产值与 raw 反事实必须调用同一公开纯函数；输入为实际 `timestamp_ms`、髋中心速度和肩髋绝对角速度，不得假设固定 FPS 或复制 summary 值。
- raw payload 必须是额外平滑前的两条序列；smoothed payload 必须包含生产使用的速度/角速度序列、稳定掩码和自适应上限。长度错位、非一维、非有限值或时间戳不严格递增必须 fail closed。
- 生产特征版本必须为 `0.2.0-provisional-envelope-evidence`，并声明 `raw_pre_extra_smoothing_and_smoothed_series_v1`；三视频重放不得改变既有 value/valid/reason/source_frames/unit。
- 当前稳定持续时间必须达到 177/177 可重放；整体覆盖为 6,591/9,971（66.101695%），32/51 特征全量可重放、19/51 不可重建。
- 当前 stability mean/P95/signed/max difference 为 34.05084746/124.2/13.85875706/251.0 ms，并绑定最大差异 video/event。它们只能作为平滑敏感性证据，不能解释为真值误差、等级差异或评分阈值。
- 当前三视频覆盖与就绪度必须绑定 M57 run SHA，仍输出 2,291 measured / 75 unavailable、834 calibration_required / 1,532 unavailable，grade=0、threshold=0；不得因证据合同升级放宽 quality gate 或推进 F3/F4。

# M58 FS02 启动方向加速度反事实验收

- `hip_acceleration_along_launch_direction_body_s2` 的 raw 对照必须同时从 raw 二维髋位置重算启动方向，并将 raw body-normalized 二维髋加速度投影到该方向；禁止复用 production smoothed 方向。
- 生产和评测必须调用同一纯函数，使用实际 `timestamp_ms` 和完整 event indexes；位置/加速度必须是对齐的二维序列，timestamp/source-frame key 不一致、索引非法或时间不递增时保持 null/reason。
- feature value 语义和 `fs01-fs02-pose-proxies-v0.5.0` 不变；新增 evidence contract 必须是 `raw_and_smoothed_hip_position_acceleration_series_v1`。三视频 M57→M58 的 value/valid/reason/source_frames/unit/feature_version 必须逐条相同。
- 当前目标特征必须达到 182/182 可重放；整体覆盖为 6,773/9,971（67.926988%），33/51 特征全量可重放、18/51 不可重建。
- mean/P95/signed/max difference 为 121.24764438/107.12051454/−115.07786512/9,311.43901824 body/s²，最大差异必须绑定 video/event。它是二阶运动学的平滑敏感性，不能用作 Pose 准确率、等级差异或 A～E 阈值。
- M58 多视频覆盖/就绪度必须保持 2,291 measured / 75 unavailable、834 calibration_required / 1,532 unavailable，grade=0、threshold=0；不得修改 quality gate、成熟度或生产标定状态。
- M58 必须通过全仓 540 项测试（15 项 optional jsonschema skipped）、M54～M58 与当前 coverage/readiness Draft 2020-12 Schema、130 个主页本地引用和 HTTP 入口检查；所有校验均不得用人工真值为空的事实冒充准确率通过。

# M59 FS09 阶段时差反事实验收

- `braking_ankle_slowdown_to_hip_deceleration_ms` 的生产计算必须调用公开纯函数，显式输入左右踝 speed、左右踝 slowdown、hip slowdown、实际 `timestamp_ms` 和 event indexes；侧别选择必须继续复用 v0.2 的净下降优先/局部 slowdown 后备语义。
- `hip_deceleration_to_double_support_proxy_ms` 必须调用公开纯函数，以左右踝 speed 构造同一事件内低运动代理，再用 hip slowdown 峰和代理起点的真实时间戳求差；不得称为真实双脚支撑、触地或承重时刻。
- 两个纯函数都必须拒绝错位长度、非一维序列、重复或逆序时间戳、越界/乱序索引；失败须返回 null 与机器原因，禁止固定 FPS、补 0 或从 summary 猜时点。
- evidence contract 必须为 `fs09_phase_timing_raw_speed_and_prepared_series_v1`；smoothed evidence 必须保存生产实际使用的 `hip_slowdown`，不得再以不相同的 `hip_deceleration` 序列冒充。braking timing 还须保存左右踝 slowdown；support timing 须保存左右踝 speed 和 low-motion mask。
- M58→M59 三视频 10,920 条 feature 的 value/valid/reason/source_frames/unit/feature_version/raw_value 必须逐条一致；只允许上述两项共 364 条 smoothed/evidence payload 改变。
- 两项有效记录必须分别达到 178/178、176/176 可重放；总覆盖必须为 7,127/9,971（71.477284%），35/51 全量可重放、16/51 不可重建。P95 raw-vs-smoothed 时差 1,137.45/916.25 ms 只能解释为平滑敏感性，不能解释为人工边界误差或准确率。
- calculation coverage/readiness 必须保持 2,291/75 feature 状态、834/1,532 score 状态和 18/57/180/1,277/834 互斥拆解；grade=0、threshold=0、F2 不变，不得放宽 quality gate 或生成 A～E。
- M59 必须通过 546 项全仓测试（15 项 optional jsonschema skipped）、三套真实 run bundle 强校验、三类当前报告 Draft 2020-12 Schema、130 个主页本地引用和两个 HTTP 入口；`compileall` 与 `git diff --check` 必须通过。

# M60 FS01-M03 完整特征族平滑反事实验收

- `bilateral_foot_rise_min_body` 与 `bilateral_foot_rise_proxy_duration_ms` 必须由 raw/smoothed 左右脚二维位置调用同一 event-local rise 纯函数；`bilateral_foot_rise_synchrony_ms` 必须使用左右脚二维速度峰的真实 `timestamp_ms`；`hip_center_vertical_velocity_body_s` 必须从髋位置按不规则时间导数计算。
- 四个纯函数必须拒绝长度/维数错位、重复或逆序时间戳、越界/乱序索引和非法 body scale。缺失不得补 0；持续时间只有在双脚和时间网格完整观测时才允许输出“观测到的 0”。
- evidence contract 必须是 `fs01_m03_raw_and_prepared_kinematic_series_v1`；raw 与 prepared 必须保存同名、同 timestamp/source-frame 网格的位置或速度序列。旧持续时间 raw payload 不得继续用从平滑位置派生的 excursion 冒充 raw。
- M59→M60 三视频 10,920 条记录的 event/Track、value/unit/valid/reason/source_frames/feature_version 必须逐条一致；raw 只允许持续时间 182 条变化，smoothed/evidence 只允许目标四项共 728 条变化。
- 四项必须分别达到 176/176、176/176、173/173、175/175 可重放；总覆盖必须为 7,827/9,971（78.497643%），39/51 全量可重放、12/51 不可重建。P95 差 0.45801831 body / 625 ms / 84 ms / 6.21942596 body/s 只能解释为平滑敏感性。
- calculation coverage/readiness 必须继续保持 2,291/75 feature 状态、834/1,532 score 状态与 18/57/180/1,277/834 互斥拆解；grade=0、threshold=0、F2 与 quality policy 不变。
- 三个 M60 run bundle、smoothing/coverage/readiness Schema、主页本地引用、HTTP 入口、`compileall`、全仓测试和 `git diff --check` 必须全部通过；当前结果为 553 tests OK（15 skipped optional jsonschema）、130 个主页引用缺失 0。这些工程验收不替代人工 Event F1、Boundary MAE、特征 MAE/P95/Bias 或教练标定。

# M61 FS01-M04 减速后稳定代理平滑反事实验收

- `bilateral_foot_vertical_slowdown_time_offset_ms`、`post_slowdown_stance_width_body`、`hip_center_lateral_variability_body` 必须调用同一组纯函数，从左右脚二维位置、髋位置和真实 `timestamp_ms` 重建共同减速锚点及后续窗口；不得假设固定 FPS、跨事件借帧或以 0 填缺失。
- 右边界截断只允许在锚点后恰好一个髋样本、且最近事件内前样本间隔不超过 160 ms 时形成双样本诊断窗口。其它短窗、错位、非法 body scale 或缺脚位置必须保持 unavailable。
- M60→M61 的 10,920 条真实 feature 记录，event/Track、value/unit/valid/reason/source_frames/feature_version 必须逐条一致；raw 仅允许站距与髋波动共 364 条扩展，smoothed/evidence 仅允许目标三项共 546 条扩展。
- 当前审计必须绑定三份 M61 feature SHA。9,971 条有效记录中 8,353 条可重放，覆盖率 83.772942%；41/51 个特征全覆盖、1 个部分覆盖、9 个未覆盖。减速时差 176/176、髋波动 175/175、减速后站距 175/176；最后一条无法形成 raw 减速锚点时必须保留 unavailable。
- 三视频运行状态不得因证据扩展改变：feature measured/unavailable 为 2,291/75，score calibration_required/unavailable 为 834/1,532，grade=0、threshold=0、13 项仍为 F2。560 tests OK（15 skipped optional jsonschema），三运行包、三类机器 Schema、130 个主页引用、两个 HTTP 入口、`compileall` 与 `git diff --check` 均通过。

# M62 FS02-M04 启动脚完整运动学反事实验收

- `launch_side_code`、`launch_foot_speed_peak_body_s`、`launch_foot_relative_displacement_body` 和 `launch_foot_motion_duration_ms` 必须调用同一个 `launch_foot_event_kinematics_from_series` 纯函数，从髋中心、左右脚位置、左右脚速度、真实 `timestamp_ms`、事件索引和 body scale 计算；不得为四项复制不同侧别规则。
- evidence contract 必须为 `fs02_m04_raw_and_prepared_launch_kinematics_v1`，四项 raw/smoothed 都必须包含五条同名二维原语序列。timestamp/source-frame 网格、维数、长度、索引或 scale 不合法时必须 fail closed，禁止固定 FPS、补 0、猜 dict 首序列或复用 production side。
- 这些量只能称为图像平面的启动脚运动学代理；不能宣称真实脚离地、触地、地面支撑或发力。raw 轨迹不能形成正向启动侧时，即使 production 平滑轨迹有侧别，也必须保留 counterfactual unavailable。
- M61→M62-v2 的 10,920 条 feature 记录必须保持 event/Track、value/unit/valid/reason/source_frames/feature_version 不变；raw 与 smoothed/evidence 各只允许四个目标特征共 728 条改变，任何非目标 payload 漂移都应拒绝。
- 当前有效记录中，启动侧必须为 179/179 可重放；峰值速度、相对位移、持续时间分别为 179/180、178/179、179/180。总覆盖必须为 9,068/9,971（90.943737%），42/51 个 Pose required feature 全覆盖、4 个部分覆盖、5 个未覆盖。
- 当前 raw/production 侧别 agreement 为 172/179；后三项 P95 差为 47.70210309 body/s、0.69326041 body、171 ms。它们只能解释为平滑敏感性，不是人工真值 MAE、准确率、等级差异或评分阈值。
- 三视频状态必须继续保持 2,291/75 feature measured/unavailable、834/1,532 score calibration_required/unavailable、grade=0、threshold=0、F2 不变；三 bundle、机器 Schema、主页引用、HTTP、`compileall`、全仓 567 项测试（15 skipped optional jsonschema）和 `git diff --check` 必须全部通过。

# M63 FS02-M05 第一阶段锚点与完整运动学反事实验收

- `launch_foot_speed_drop_body_s`、`first_step_displacement_body`、`post_step_hip_direction_consistency`、`launch_foot_slowdown_to_post_hip_direction_ms` 与 `post_step_stance_width_body` 必须调用同一个 `first_step_phase_kinematics_from_series` 纯函数。输入必须包含髋位置/速度、左右脚位置/速度、真实 `timestamp_ms`、事件索引、body scale 和版本化关键阶段。
- evidence contract 必须为 `fs02_m05_raw_and_prepared_first_step_phase_kinematics_v1`。五项 raw/smoothed 都必须包含六条同名二维原语序列；timestamp/source-frame 网格、维数、长度、索引、scale 或阶段非法时 fail closed，禁止固定 FPS、补 0、猜 dict 首序列或借用 production side。
- 后阶段三项必须固定事件记录的 `first_step_slowdown_proxy_ms`；raw 反事实只能替换运动学序列，不得重新选择阶段并形成混合反事实。旧产物只有在明确声明同义 fallback phase 时才允许兼容。
- 五项只能称为第一步候选阶段的 2D Pose 运动学代理，不得宣称真实落地、触地时刻、足底压力、冲击或地面作用力。
- M62-v2→M63 的 10,920 条 feature 记录必须保持 event/Track、value/unit/valid/reason/source_frames/feature_version 不变；raw 与 smoothed/evidence 各只允许五个目标特征共 910 条改变，非目标 payload 变化必须为 0。
- 五项可重放计数必须分别为 179/180、179/180、180/180、180/180、179/179。前两项唯一一条 raw 无正向启动侧时必须保留 unavailable；不得为了达到 100% 覆盖而倒填侧别。
- 总覆盖必须为 9,965/9,971（99.939825%），45/51 个 Pose required feature 全覆盖、6 个部分覆盖、0 个完全未覆盖。五项 P95 平滑差 50.75936567 body/s、0.52313353 body、0.22365749、84.75 ms、0.41392841 body 只表示平滑敏感性。
- 三视频状态必须继续为 2,291/75 feature measured/unavailable、834/1,532 score calibration_required/unavailable、grade=0、threshold=0、13 项 F2。三 bundle、机器报告、动态页面、`compileall`、全仓 574 项测试（15 skipped optional jsonschema）和 `git diff --check` 必须通过；这些工程验收不替代人工 Event F1、Boundary MAE、特征 MAE/P95/Bias、多教练标定或独立测试。

# M64 F2 误差预算就绪矩阵验收

- 报告必须绑定当前 feasibility registry、M63 smoothing coverage、M63 multivideo calculation coverage、三段完整 run 的 frames/primary-player/events，以及 manual events/keypoints/semantics 的路径和 SHA；视频集合必须精确相等。
- validator 必须重新执行 smoothing 与 calculation coverage 的 source replay，并以三套当前 run 重新执行 `build_scoring_truth_evaluation`。不能信任输入报告中自报的 ready、accuracy 或 promotion 状态。
- registry 的 51 个 Pose required feature 必须与 smoothing report 的 feature set 精确相等；13 个指标必须与 calculation coverage 精确相等。上下文特征须单独列出，不得伪装为 Pose 平滑缺失。
- 每个指标必须分别报告 calculation、no-extra-smoothing counterfactual、Event F1/IoU/Boundary MAE、feature MAE/P95/Bias、Pose error、event-boundary error、truth-conditioned smoothing error、missing-value impact 和 external grade-gap assessment 状态。
- 当前真实结果必须为 10/13 指标 smoothing complete、3/13 partial、0 个完全不可重放；人工 events/keypoints/semantics 均为 0，所有真值误差与 grade-gap 证据均未就绪，F2→F3 ready 必须为 0/13。
- 报告必须强制 `candidate_events_are_ground_truth=false`、`measurement_coverage_is_accuracy=false`、`smoothing_counterfactual_is_accuracy=false`、grade/threshold/accuracy/promotion claims 全 false，成熟度上限为 F2。
- 输出必须不可覆盖、原子落盘；来源、计数、安全字段或逐指标 blocker 被篡改时 source replay 必须拒绝。机器 Schema、动态报告、本地链接、HTTP、`compileall`、全仓测试和 `git diff --check` 必须通过。

# M65 三视频评分阻断归因验收

- v1.1 多视频就绪报告对 2,366 条指标实例执行 source replay，并把 `reason_codes` 精确反查到原始评分 flag；所有类型化原因均有支撑。
- 1,277 条完整评分向量因证据门禁 fail closed。关键点跳变是 645 条的唯一阻断，左右交换是 173 条的唯一阻断；这些计数只代表人工复核优先级。
- 三段完整视频共生成 1,516 个去重 Pose 诊断候选任务，并生成三份全时间线空白 truth pack；候选不是人工真值。
- 当前 coverage=0、precision/recall/F1=`null`，聚合审查为 `annotation_and_protocol_required`；quality policy、grade、threshold 和 F2/F3/F4 均未改变。
- 验收验证：全仓 581 tests OK（16 个可选依赖跳过）；11 份 M65 JSON 通过 Draft 2020-12 Schema；队列、truth pack 和多视频拆解通过来源 SHA 重放；5 个 HTML 的 140 个本地引用缺失 0，6 个本地 HTTP 入口返回 200。

# M66 三视频小 ROI 测量恢复验收

- 基线必须同时报告 feature vector 与 operational measurement：当前为 2,297/69 complete/incomplete，保留原 measurement gate 后为 2,291/75 measured/unavailable；两者不得混称。
- 实验只允许选择仍有主球员检测、仍在原调度内、且由生产 32px ROI size guard 跳过的帧。视频 1 合格目标为 0；视频 2/3 为 131/18，总计 149，禁止按恢复结果二次筛选。
- 8px 实验必须保持同一视频、检测、主球员时间线、事件边界、RTMPose-M Halpe26 权重、特征公式和质量策略。149/149 帧产生 Pose，恢复 22 条特征向量、20 条 operational measurement，两个口径均为 0 回归。
- 实验投影为 2,311 measured / 55 unavailable；18 条 measurement hard fail 不变，另有 37 条非 hard-fail 特征缺口。该投影不得覆盖当前生产 2,291/75，不得进入标定或评分。
- 两段动态 A/B 必须覆盖全部受影响事件窗口，而不是单张截图；左侧保持 32px，右侧仅改为 8px。H.264、首/中/末帧可解码、视频/实验报告 SHA 绑定必须通过。
- 所有产物必须声明 accuracy、truth、production、automatic fallback、gate modification、grade、threshold 和 maturity promotion 均为 false。人工关键点误差和独立发布审核前不得切换生产路由。
- 验收验证：全仓 588 tests OK（16 个可选依赖跳过）；6 份 M66 JSON 通过 Draft 2020-12 Schema，聚合报告从源文件和固定边界比较重放而不是信任自报恢复数。

# M67 ROI margin 与组合观测恢复验收

- margin 实验必须保持同一 RTMPose-M Halpe26 256×192 权重、检测、Track、主球员时间线、候选事件、特征公式和质量门禁，仅把 ROI margin 从 0.15 改为 0.30；不得在 baseline Pose 缺失的帧冒充同类实验。
- 三视频目标必须固定为 410 帧且 410/410 有实验 Pose。固定边界结果必须分别报告 feature-vector 恢复 14/回归 1 与 operational 恢复 11/回归 1，不能只报告净增益。
- 有效关键点计数增加/减少/不变必须为 76/107/227 帧；该计数不是准确率，也证明 margin 增大不具单调性。仅筛选 baseline ROI clipping 的 233 帧仍有 1 条 operational 回归，不能成为生产 fallback 条件。
- M66 与 M67 目标集必须在每段视频内互斥。组合结果必须把两类 Pose 合回完整帧再运行一次特征计算，禁止直接相加恢复计数；目标 559 帧，结果必须为 feature 2,331/2,366 complete、operational 2,320/2,366 measured，恢复 30、回归 1，hard fail 18、非 hard-fail 特征缺口 28。
- 三段动态对比必须是 H.264 视频并通过首/中/末解码与报告 SHA 绑定；不得只提供截图。主报告必须直接链接三段视频和机器汇总。
- 只要存在回归、人工关键点真值缺失或独立视频发布测试未完成，生产必须继续使用 0.15 margin、32px 最小 ROI、无自动 fallback；grade=0、threshold=0、F2 不晋级。

# M68 required-joint 严格超集路由验收

- 路由关节集合必须由当前 13 项 registry required feature 解析，当前精确为左右肩、髋、膝、踝共 8 点；未知 feature 映射必须 fail closed。
- 候选 Pose 只有在 required-joint 有效集合严格包含 baseline 集合、且没有丢失任何 baseline-valid required joint 时才能选中。选择不得读取 feature value、事件结果、评分状态、grade 或 threshold。
- 三视频 410 个 margin 候选帧必须选中 44、拒绝 366；与 M66 合并后实际实验路由为 193 帧。各视频来源与固定边界报告必须以 SHA256 绑定，禁止算术拼接恢复计数。
- 固定边界重算必须得到 feature 2,329/2,366 complete（恢复 32、回归 0）与 operational 2,320/2,366 measured（恢复 29、回归 0）；hard fail 18、非 hard-fail 特征缺口 28。
- 三段动态对比必须为 H.264 并通过首/中/末解码、视频 SHA 和 experiment report SHA 绑定；主报告须可直接播放或打开视频。
- “当前三视频零回归”必须明确不是准确率或独立验证。人工校正关键点、分视角特征误差和预注册独立视频测试缺失时，生产继续使用 0.15 margin、32px 最小 ROI、无自动路由；grade=0、threshold=0、F2 不晋级。
- 验收验证：全仓 601 tests OK（16 skipped）；3 份实验报告和聚合报告通过 Schema、validator 与 source replay；三段 H.264 为 44/336/66 帧且首/中/末可解码；主页 163 个本地引用缺失 0，本地 HTTP 主页与视频返回 200。

# M69 残差高分辨率 Pose 路由验收

- 残差审计必须从 M68 固定边界结果重放：28 条非 hard-fail feature-incomplete 指标实例、13 个事件、258 个目标帧；不得把 18 条 measurement hard fail 伪装成可恢复目标。
- 高分辨率候选只在目标帧运行；258 帧必须为 255 个 Pose 输出、3 个缺失。候选同时改变 384×288 Pose 权重/输入和 0.30 ROI 上下文，报告必须禁止单因素因果归因。
- required-joint 路由必须保持严格超集合同：255 个候选输出最终只允许 40 帧进入组合，218 帧拒绝；选择不得读取 feature value、事件结果、grade 或 threshold。
- 固定边界重算必须分别报告 feature 2,335/2,366 complete 与 operational 2,326/2,366 measured；两者均恢复 6、回归 0。hard fail 保持 18，非 hard-fail feature incomplete 从 28 降到 22。
- 三段动态对比必须为 H.264 视频并绑定 baseline/routed frames、残差审计与 experiment report SHA；视频只展示实际改变的事件窗口，不能以截图替代。
- 当前三视频零回归不是准确率或独立验证。人工校正关键点、分视角特征误差、单因素消融与预注册独立视频测试缺失时，生产不得自动切换 profile；grade=0、threshold=0、F2 不晋级。
- 验收验证：全仓 607 tests OK（16 skipped）；5 份 M69 机器文档通过 Schema、validator 与 source replay；三段 H.264 为 43/58/33 帧并通过解码、SHA 与 HTTP；主报告 175 个本地引用、94 个唯一目标，缺失 0，浏览器内嵌视频可加载。

# M70 Pose profile / 裁剪上下文消融与锚定扩展验收

- 必须重放 M68 的逐帧实际裁剪来源，而不是把所有残差帧强制改成同一个 ROI：258 帧精确分为 baseline 179、0.30 margin 28、8px 小 ROI 51；分类不得读取 feature、event outcome、grade 或 threshold。
- 同上下文 384×288 候选必须只改变 Pose profile。258 帧中 254 帧产生 Pose，required-joint 严格超集路由选中 32 帧；固定边界 feature 恢复 4、operational 恢复 2、回归 0。该结果证明 M69 的 6 个恢复不是纯分辨率效应。
- 直接多候选择优必须逐实例审计，不得只看总数。当前选中 42 帧、operational 恢复仍为 6、相对 M68 回归 0，但丢失 1 个 M69 已恢复实例并在另一事件补回 1 个，因此必须拒绝。
- 锚定扩展必须以 M69 路由结果为 baseline，只接受同上下文候选的 required-joint 有效集严格超集。三视频额外选择 15 帧，必须保留全部 M69 feature/operational 恢复，lost count 均为 0。
- 固定边界锚定投影必须为 feature 2,338/2,366 complete（相对 M68 恢复 9、回归 0）与 operational 2,327/2,366 measured（恢复 7、回归 0）；相对 M69 新增 1 个 operational 恢复。hard fail 保持 18，非 hard-fail 残差为 21。
- 视频 1/2 必须提供 M69 vs M70 的 H.264 全时序动态对比并绑定 experiment report SHA；视频 3 选择 0 帧，不得伪造变化视频。浏览器必须实际达到 `readyState=4` 且无媒体错误。
- “当前三视频逐实例零回退”仍不是关键点准确率或独立验证。人工校正关键点、分视角特征误差和预注册独立视频测试缺失时，生产、F2、grade=0、threshold=0 均不得改变。
- 验收验证：全仓 614 tests OK（16 skipped）；7 份 M70 机器报告通过 validator、Draft 2020-12 Schema 与 source replay；两段视频为 57/58 帧 H.264；主报告 183 个本地引用、97 个唯一目标，缺失 0，浏览器内嵌视频可播放。

# M71 RTMPose-L 受控残差扩展验收

- 候选必须是版本化的官方 RTMPose-L Halpe26 384×288，checkpoint SHA256 固定为 `734182CE2409BA84E96EA2A6361AEDB0EB40722F7AB52BB69331C73477D6A650`；模型文件、字节数、配置名、输入尺寸或注册表不一致必须拒绝。
- 只能运行 M70 冻结的 258 个残差帧，并复用各帧 M68 的真实 crop context；254 个候选输出不得被称为 254 个正确 Pose。
- 路由 baseline 必须是 M70 frames。只有 candidate 的 registry-required joint 有效集合严格包含 baseline 且没有必需关节回退时才能选中；不得读取 feature value、event outcome、grade 或 threshold。
- 三视频必须选择 46 帧；相对 M68 feature 恢复 15/回归 0、operational 恢复 13/回归 0；最终 feature complete 2,344/2,366、operational measured 2,333/2,366。相对 M70 必须额外恢复 feature/operational 各 6，lost M70 recovery 为 0。
- 18 条 measurement hard fail 必须保持不变；非 hard-fail operational 残差为 15。不能通过放宽 feature/quality/scoring gate 获得恢复数。
- 视频 1/2 必须提供 63/88 帧 H.264 全时序动态对比，验证文件绑定视频、baseline/experimental frames 和 per-video report SHA，并通过首/中/末解码；视频 3 选择 0 帧，不得生成伪变化视频。
- 该三视频零回归结果不是关键点准确率、事件准确率或独立发布验证。人工校正关键点、分视角 MAE/P95/PCK 和预注册独立视频测试缺失时，生产默认、自动 fallback、F2、grade=0、threshold=0 均不得改变。
- 验收验证：全仓 618 tests OK（16 skipped）；3 份 per-video 报告与 1 份聚合报告通过 Python validator、Draft 2020-12 Schema 和 source/artifact SHA replay；两段视频 H.264 63/88 帧，首/中/末解码与浏览器 `readyState=4` 均通过。

# M72 同拓扑缺失关键点加法融合验收

- 融合必须要求 M71 baseline 与 RTMPose-L candidate 的视频、帧、人物、keypoint topology/order 和全部非 Pose 字段精确相同；任何漂移均 fail closed。
- 只允许填补 M71 无效的 registry-required joint 与左右 big toe/small toe/heel。所有 M71 已有效关键点的坐标、置信度、valid 与其他字段必须逐字节保持，覆盖计数必须为 0。
- 选择不得读取 feature value、event outcome、grade 或 threshold；不得降低 feature、measurement、quality 或 scoring gate，不得改变事件边界。
- 三视频必须为 46 个 changed frames、79 个新增有效关节观测。固定边界重算必须得到 feature complete 2,346/2,366、operational measured 2,335/2,366；相对 M71 各恢复 2、回归和丢失均为 0。
- 18 条 measurement hard fail 保持不变，剩余 13 条 non-hard-fail operational residual 必须精确分类为事件内覆盖不足 9、视频起点截断 2、必需阶段代理未观测 2。禁止用 0、跨界借帧、弱化 valid fraction 或虚构 phase 消除这些 unavailable。
- 补入 79 点只恢复 2 个指标实例，必须明确“点数不是准确率或评分恢复率”。候选补点没有人工真值时不得进入生产、标定数据集或 F3/F4 晋级。
- 仅视频 1 产生 FS01-M02/FS02-M02 恢复，因此只生成一段 76 帧、2.535091 秒 H.264；视频 2/3 不得生成伪变化视频。主报告必须直接链接该视频和机器汇总。
- 验收验证：全仓 624 tests OK（16 skipped）；3 份逐视频报告与 1 份汇总通过 Python validator、Draft 2020-12 Schema 和 source/artifact SHA replay；视频在浏览器内 `readyState=4` 且无媒体错误。生产默认、grade=0、threshold=0、13 项 F2 均不改变。

# M73 WholeBody133 跨拓扑同名点融合验收

- 候选必须绑定官方 `rtmpose-m-coco-wholebody133-256x192`，checkpoint SHA256 为 `3DA02694CD6479D3B333FF42EBD0723F96BFA06ADAC1DB1E2E815ED2E9E1B02D`、原生拓扑 133 点；模型、配置、字节数或注册表不一致必须拒绝。
- 只能处理 M71/M72 冻结的 258 个残差帧并逐帧复用原 crop context。不得根据 WholeBody 输出、特征值或恢复结果扩展/缩小目标集。
- baseline 必须是 M72。跨拓扑只允许显式一对一同名映射：左右肩、髋、膝、踝、big toe、small toe、heel；M72 所有有效点和 Halpe26 topology/order 必须保持，覆盖计数为 0。
- 三视频必须得到 254 个候选输出、49 个 changed frames、101 个新增有效点；逐视频点数为 85/16/0。候选点数量不得称为准确率或评分恢复率。
- 固定边界重算必须诚实保留负结果：相对 M72 新增 feature complete=0、operational measured=0、回归=0、lost recovery=0；最终仍为 2,346/2,366 feature complete、2,335/2,366 operational measured、18 hard fail、13 non-hard-fail residual。
- 剩余 13 条必须继续精确分类为事件内连续观测覆盖不足 9、视频起点边界截断 2、必需阶段代理未观测 2。不得通过多点模型的存在伪造连续性或阶段证据。
- 视频 1/2 必须发布 112/130 帧 H.264 动态对比并绑定各自 report、baseline/experimental frames 和 ffmpeg SHA；视频 3 无变化不得伪造视频。
- 验收验证：全仓 630 tests OK（16 skipped）；3 份 per-video 报告和 1 份汇总通过 Python validator、Draft 2020-12 Schema 与 source replay；两段视频浏览器 `readyState=4`、媒体错误为空。生产、F2、grade=0、threshold=0 均不改变。

# M74 事件内有界 Pose 缺口反事实验收

- 输入必须精确绑定 M73 汇总、三份 M73 per-video report、融合 frames、primary timeline、events 与当前 registry 的路径和 SHA；残余集合必须仍为 13 个实例，任何 replay 漂移都应拒绝。
- 插值只能发生在同一事件内部且缺口左右均为 confidence≥0.25 的真实有效观测；两个边界观测的 timestamp 跨度必须≤160 ms。必须使用 `timestamp_ms` 线性插值，禁止固定 FPS、事件边界外推、跨事件借帧、补 0 或创建关键阶段。
- 每个插值点必须记录 event、joint、source frame、timestamp、插值坐标、左右边界证据帧/时间/置信度和有效置信度来源；有效置信度只能取两端真实观测的较小值，且必须注明这不是模型置信度。
- 当前逐指标 replay 为 226 个插值关节观测，去重后 142 个 event×joint×frame；157 个可插值运行跨度 66～134 ms。20 个 invalid feature 转为 valid，反事实完整向量为 7/13，剩余 6/13。
- 该 7/13 只能称为反事实可计算上限。production recovered 必须为 0，不得生成生产 Pose artifact，不得改变原 feature/measurement/scoring 状态、F2、grade 或 threshold。
- 人工关键点真值、分视角 MAE/P95/Bias、事件边界误差和独立视频测试缺失时，插值不得进入 Worker、标定数据集或 production scoring；下一里程碑应建立 142 点的盲化真值包。
- 验收验证覆盖不规则 timestamp、内部短缺口、首尾缺口、拓扑缺失、source SHA replay、机器 Schema、主报告链接、`compileall` 与 `git diff --check`；全仓 636 tests OK（16 skipped），主页 206 个本地引用/106 个唯一目标/缺失 0，浏览器既有 23 段视频全部 `readyState=4` 且无媒体错误。

# M75 事件内短缺口关键点真值验收

- 任务集合必须从 M74 的 142 个唯一 `event×joint×frame` 候选精确派生，当前只覆盖存在合格候选的两段视频、54 个源帧。不得为视频起点缺口或缺失事件阶段伪造插值真值任务。
- 浏览器 bootstrap 不得包含插值 x/y、候选置信度或密封预测文件；只允许加载无骨架原视频、任务关节和标注视图。密封预测必须单独保存并由 manifest canonical SHA 与文件 SHA 双重绑定。
- 每个任务必须有两名不同 annotator 的独立记录；accepted adjudication 必须由未参与这两份记录的 reviewer 完成，并精确绑定 source annotation IDs。可见点使用 `[0,1]` 归一化坐标，不可见点使用 null 和原因，禁止以 0 填缺失。
- 当前 Pose 缺失帧允许用 160 ms 内双侧主球员框并集生成 annotation-view-only 裁剪；必须记录端点帧、Track 和跨度，并声明这不是身份或关键点真值。
- 只有 142/142 任务完整裁决后才可输出插值 MAE、P95、x/y Bias、valid rate 和按关节/视角/事件族结果。部分标注、工作台草稿或 synthetic contract fixture 不得产生真实误差结论。
- 即使真实误差报告完成，也必须经过外部预注册接受协议及后续特征误差重放；M75 不得自动启用生产插值、修改 quality gate、生成 A～E/threshold 或推进 F3/F4。

# M76 真值条件特征误差验收

- 固定 M74/M75 来源并逐点重放 142 个有界插值候选，只替换缺口坐标，不替换非缺口 Pose、事件边界或 Track；
- 固定评测 11 个指标实例、47 条 required-feature 记录和 25 个唯一特征；2 个缺 required phase 的 FS09 实例必须显式排除；
- 数值特征输出 MAE/P95/Bias，角度采用环形差，code 只输出一致率；不可见点为 NaN，禁止以 0 表示缺失；
- 当前人工裁决为 0，真实报告 status=`annotation_required`，metrics/per-feature/per-indicator 均为 null；
- 合成契约只验证连线和数学完整性，不构成真实模型效果；无外部预注册接受协议时，生产插值、A～E、threshold 和 F3/F4 晋级必须保持关闭。
- 专项 5/5，真实 JSON Schema 与来源重放通过；全仓 647 tests OK（16 skipped）。重建主报告 214 个本地引用/110 个唯一目标缺失 0，应用内浏览器已核验 M76 范围与安全状态。

# M77 FS09 阶段真值验收

- 精确覆盖 M74 的 2 个 FS09-M05 `required_pose_phase_proxy_not_observed` 实例，不扩大到其他事件或指标；
- 工作台只显示无骨架、无音频、无候选叠加的 H.264 审阅短片；候选 event_id/边界/阶段保持密封，短片局部时间必须通过绑定 offset 换算为原视频绝对 `timestamp_ms`；
- 事件边界与 peak/deceleration/restabilization/stable-control 阶段均需双人独立标注和第三方裁决，不可观测/未出现必须显式给原因；
- 评测输出 Event IoU/Boundary MAE/Bias、阶段 MAE/Bias 及同模型 Pose 下的人工边界条件特征差；后者不得称为总特征误差；
- 人工 stable control 不是接触/双支撑/负荷真值，不得用于伪造 `hip_deceleration_to_double_support_proxy_ms`；
- 当前真值为 0，报告 status=`annotation_required`，全部 metrics=null，运行时事件/特征、grade、threshold 和 F3/F4 均不改变。
- 当前真实状态必须为 annotations=0、accepted adjudications=0、`annotation_required`、metrics=null；M74 production recovered 继续为 0。
- 验收验证覆盖密封预测篡改、同步重算 manifest SHA 后的 M74 source replay、重哈希伪造视频 probe、同人裁决、不可变输出目录、synthetic-only 零误差计算、JS 语法、四个 Draft 2020-12 Schema、主报告本地链接、浏览器视频解码、`compileall` 和 `git diff --check`。最终全仓 655 tests OK（16 skipped）；主报告 220 个本地引用/113 个唯一目标缺失 0；工作台 2 个任务的短片分别为 3.875/3.542 秒，均 `readyState=4`、媒体错误为空，短片 0 ms 映射为原视频 331458/450083 ms，bootstrap 无密封预测字段。

# M78 类型化评分阻断验收

- scoring block flag、typed reason、人工 truth requirement 与 blocker group 必须来自同一版本化机器 taxonomy；评分、readiness、blocker audit 和人工任务路由不得保留第二套手工映射。
- 关键点跳变、左右点交换和目标方向缺失不得映射为 `event_identity_continuity_unverified`；身份原因只能由 source Track 切换候选或主球员选择歧义支撑。
- 每个真实活动 flag 必须有且只有一个 typed reason 和 truth requirement；每个输出的已知 typed reason 必须能反查到同一记录的活动 flag。未知 flag 必须 fail closed 到外部人工复核。
- 三视频无 GPU 重放必须保持事件键、非 confidence 特征/指标/score 载荷和 score status/grade/reason/feedback/quality gate 一致。confidence 序列化末位差的最大绝对值不得超过 0.0000011，并必须注明它不是评分阈值。
- 当前 2,366 条实例仍必须为 834 calibration_required / 1,532 unavailable、grade=0、threshold=0；taxonomy 变更不得修改 measurement/scoring gate、解除 unavailable 或推进 F3/F4。
- 阻断计数允许同一实例多标签重叠，任何表格不得把 flag 次数求和当作唯一指标实例数或准确率；恢复优先级只能用于安排人工真值工作。
- 验收验证：全仓 663 tests OK（16 skipped）；三套无 GPU 重放 bundle 均通过跨产物 validator；taxonomy 与 replay audit 通过 Draft 2020-12 Schema；主报告 222 个本地引用/114 个唯一目标、缺失 0。

# M79 跳点/左右交换动态视频双盲真值验收

- 任务集合必须从 M78 绑定的三份 M65 队列重新投影，精确等于 1,371 个 score-relevant 候选：jump 1,017、swap 354；覆盖 1,336 个去重指标实例。同步修改候选并重算 SHA/canonical hash 仍必须由 queue replay 拒绝。
- 三段完整视频的候选窗口必须合并为 103 个片段、9,636 帧，并逐片段保存为独立 H.264/yuv420p MP4；每个文件必须绑定 SHA、帧数、FPS、分辨率和首/中/末帧解码。
- 第一阶段 review bootstrap 不得包含候选 task ID、候选帧、候选 joint/pair、受影响指标、模型 Pose 或密封候选文件引用。两名标注者必须使用不同 ID，分别保留并导出自己的 coverage 与 positive 原始行。
- 第二阶段必须是独立页面；只有导入恰好两名不同 annotator 的原始 coverage/positive 后，第三名 reviewer 才可查看密封候选并裁决。reviewer 与 annotator 重合、引用行不完整、confirmed true 没有精确 raw positive 时必须 fail closed。
- partial annotations 不得输出任何指标。全部 1,371 项完成后也只允许输出 candidate precision；候选窗口不具备全时间线负例覆盖，recall/F1 必须为 null。
- M79 不得自动解除 quality/scoring gate、修改运行候选、生成 A～E/threshold 或推进 F3/F4。当前真实包必须为 coverage/positive/adjudication=0/0/0、`annotation_required`、全部 metrics=null。
- 本地服务必须返回 HTTP 206 和 `Accept-Ranges: bytes`；应用内浏览器要验证首片段逐帧前进、最长视频第 77/77 片段直接加载、裁决页候选时间精确定位，并且媒体错误为空。

# M80 第三方动态骨架裁决验收

- 第一阶段继续禁止候选 ID/帧/joint、模型坐标、pose evidence URL 和骨架叠加；M80 不得以提高可用性为由破坏两名 annotator 的独立盲化。
- 第二阶段的 103 份 pose evidence 必须与 103 个 H.264 片段一一对应，覆盖相同 9,636 帧；每帧必须绑定源帧、timestamp、primary source Track、Pose presence、Halpe26 点坐标与置信度。
- 浏览器必须校验 sidecar 文件 SHA-256；机器 validator 必须从 `frames.jsonl` 与 `primary-player.jsonl` 逐帧重建 exact evidence。修改坐标后同步重算文件 SHA/canonical SHA 仍必须 fail closed。
- reviewer 页面必须同步显示完整骨架、候选目标 joint/pair、前后轨迹、置信度和源帧；叠加层必须明确标为模型输出而非人工真值。
- 候选集合、双人 coverage/positive、第三人 adjudication 和 precision-only 限制保持不变。当前 coverage/positive/adjudication=0/0/0，metrics=null；quality/scoring gate、grade、threshold 和成熟度均不改变。
- 机器验收包含 103/103 sidecar Schema、7 项定向测试、浏览器两页视频解码、骨架 canvas、候选跳转和第一阶段泄漏检查。最终包为 `data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2/`。
- 最终全仓回归为 670 项通过、16 项可选依赖跳过；主报告 225 个本地引用、117 个唯一资源均可解析，103 段 M80 媒体与 M79 源片段逐文件 SHA-256 完全一致。

# M81 三视频动态评分观察器验收

- 必须使用当前三段真实评分 bundle，覆盖 15,868 帧、546 个候选事件和 2,366 条指标实例；不得从历史 YOLO 或过期 registry 产物拼接状态。
- 原视频、Halpe26 骨架、FS01/FS02/FS09 时间轴、关键阶段、指标 required joints、特征 value/unit/confidence/valid/reason/source_frames 和门禁原因必须随播放联动。
- 竖屏与横屏视频必须保持原始宽高比，canvas 与视频显示区域一一重合；三段视频都必须 `readyState=4` 且 `error=null`。
- feature status 与 scoring status 必须分列；汇总必须保持 2,291/75 feature measured/unavailable、834/1,532 scoring calibration_required/unavailable、0 grade、0 threshold。
- 浏览器必须校验每个实际 JSON 数据文件 SHA-256；机器 validator 必须绑定视频、frames、primary timeline、events、indicator-features、scores、summary 与 registry，并拒绝坐标或状态篡改。
- 页面必须明确 Pose overlay 与事件区间都不是人工真值，不能输出总分、准确率或 F3/F4 结论。入口为 `reports/scoring-visual-observer-m81/index.html`。
- 实际验收结果：三视频浏览器联动通过；主报告 226 个本地引用与观察器 2 个静态引用缺失均为 0；全仓测试 674/674 通过，另有 16 项可选 `jsonschema` 检查跳过。

# M85 Registry lifecycle 生产门禁验收

- `registry-lifecycle.json` 必须以固定角色登记唯一 `runtime_feasibility` 和
  `current_scoring_requirements`，并以 raw SHA、内嵌版本、来源版本和受限相对路径绑定实际
  文件；禁止 `latest`、目录扫描、未登记回退或路径逃逸。
- 旧六指标 `metric-feasibility.json` 必须为 `historical`；仍绑定 `.8` feasibility 的
  `metric-measurement-plans.json` 必须为 `planning_only`。两者都不能通过生产 role API。
- Vision CLI、API/Worker、直接 `run_pipeline`、离线 calibrated rescoring 和生产标定晋升必须消费同一次
  bytes 快照验证后的 payload。请求或 registry 参数只能作当前角色精确路径 pin；历史文件、
  未登记文件和其他路径的同字节副本必须在写入前拒绝。
- Job request 不能选择 manifest；manifest、配置 pin 与解析出的 registry 都不能位于 job 可写
  目录。容器镜像必须包含 manifest 与两个当前角色工件，并保持 authority 文件 root-owned。
- 四个旧六指标脚本必须显式传 `--historical-replay`，同时固定旧版本、六个 ID 和 raw SHA，
  使用已验证 payload，并验证复用/报告内的视频、事件、精确指标集合、registry 版本与空等级；
  当前 `.17` 产物不得重贴历史标签，且所有输出标记 `historical_replay`。
- 最终聚焦回归 75/75；全仓 `Ran 735 tests in 252.842s`，全部通过、无 skip。13 项仍全部
  为 F2，真实教练真值、正式阈值、grade 和 F3/F4 晋级均未被创建。
