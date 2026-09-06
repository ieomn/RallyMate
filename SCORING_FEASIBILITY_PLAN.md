# RallyMate 最小可行评分闭环实施计划

文档状态：已确认并按里程碑实施；F3/F4 等待外部真值  
计划版本：`1.52.0-implemented`  
审计基线日期：2026-08-12  
代码基线：`rallymate-vision-demo 0.2.0`  
现有观测契约：`1.0.0`  
现有评分卡注册表：`2026-08-10-demo.1`  
范围：固定机位、单个主球员、Pose-only；仅 FS01、FS02、FS09。最初六项保持 COCO-17 兼容，当前 Pose Wave v2 使用 Halpe26/WholeBody133 足部点扩展到 13 项 F2。

## 0. 2026-08-13 实施记录

用户以“继续”确认按本计划推进。当前完成状态如下：

| 里程碑 | 状态 | 核心产物 | 当前外部阻断 |
|---|---|---|---|
| M0 审计与计划 | completed | 本文件、技术手册审计、冻结视频基线 | 无 |
| M1 F0～F4 与安全状态机 | completed | 初始 `metric-feasibility.json`、当前 `metric-feasibility-pose-wave-v2.json`、Schema、`score_indicator` | F3/F4 验收目标仍为 `null` |
| M2 稳定主球员 | completed | `primary-player.jsonl`、全局/事件内诊断 | confirmed ID Switch 需身份真值 |
| M3 Pose-only 事件 | completed_at_rule_baseline | `events.jsonl`、人工标注导入、F1/IoU/Boundary MAE 评测器 | 真实事件标签缺失 |
| M4 初始十特征与六指标 | completed_at_F2 | 版本化纯函数、单位、有效性、原始/平滑值、证据帧 | 人工校正关键点缺失 |
| M5 特征误差预算 | interface_completed | MAE/P95/Bias/有效率/分视角及四类误差预算 | 真实关键点与事件真值缺失 |
| M6 标定接口 | interface_completed | 多教练一致性、阈值规则、序数回归统一契约 | 没有教练标签/标定资产 |
| M7 报告与 E2E | completed | 任务管线、JSONL、HTML 时间轴、真实 GPU 120 帧烟测 | F4 独立测试未开展 |
| M8 Pose 模型替换与动态对比 | candidate_integrated | 历史 YOLO/三档 RTMPose 六指标 A/B、97 秒四宫格视频、足部诊断特征 | RallyMate 人工关键点/事件真值缺失 |
| M9 RTMPose 服务部署预设 | candidate_integrated | 256 在线档、384 分析档、YOLO 回滚档、Persistent Worker 双档烟测 | 尚未 F4 晋级，默认不自动切换 |
| M10 真值采集与防泄漏 | tooling_completed_annotation_pending | 完整视频事件标注、密集关键点裁决、三教练标签真值包；未标注模型点不再混入真值 | 需要实际标注者完成数据 |
| M11 用户可见评分语义 | completed | API、Summary、298 项审计和 HTML 统一区分 `measured / calibration_required / unavailable / scored`；本轮 13 项不显示为 0 分，其他 285 项明确为不在本轮闭环 | A～E 仍需实际教练标定与独立测试 |
| M12 晋级与跨产物安全门禁 | completed | 标定契约 1.2.0 强制独立测试凭证及特征单位/版本绑定；生产资产还必须经过受控可信晋级账本授权；F0→F4 仅可逐级晋级；`validate_run.py` 校验 Track/事件/特征/指标/Score/Hash 全链路 | 真值包仍需实际标注，13 项保持 F2 |
| M13 Pose Wave v2 | completed_at_F2 | v2 注册表共 13 项；新增 FS01-M03/M04、FS02-M03/M04/M05 五项足部/下肢运动学代理；Halpe26/WholeBody133 同窗产物 | 接触、受力和战术语义不可由 2D Pose 直接观测；真值与独立测试缺失 |
| M14 真值到标定资产的安全链路 | interface_completed_truth_pending | 离线事件/关键点/语义工作台；人工边界特征重算；精确关联数据集；无泄漏拆分与 test seal；阈值/序数候选拟合；独立测试从原始教练标签重算结论；人工晋级；可信账本授权后 Pipeline 加载 | 人工事件/关键点/语义/教练标签仍为 0，13 项仍为 F2，未生成 production 资产或可信账本 |
| M15 成熟度证据与生产绑定 | completed_at_contract_level | 版本化 F0→F4 maturity-evidence、全局最优事件真值匹配、全 13 项双后端 synthetic test-only 契约、promotion/ledger/运行时注册表精确绑定 | 真实 maturity evidence、教练真值、独立测试和 F4 发布均不存在 |
| M16 候选事件、身份与产物完整性加固 | completed_at_F2_safety_level | 静止/共模抖动负例门禁；`primary-player-v0.2.0` 确定性选择与歧义诊断；注册表/时序版本 fail-closed；必需阶段、质量门禁和跨 JSONL 哈希一致性；排序标注隔离后端 | 规则候选仍需人工事件真值；排序后端只输出相对顺序，不输出 A～E |
| M17 生产运行配置与视角信任绑定 | completed_at_contract_level | 生产标定除晋级账本和 F4 registry 外，还必须精确绑定 Pose backend/profile/model SHA/原生拓扑、事件/阶段/主球员/质量策略/逐指标特征合同，以及经人工接受的逐视频视角证据；离线批处理、Pipeline 与 Service 均 fail-closed；v0.6 真实 bundle、固定边界 A/B、空白标定数据集和报告已统一重生成 | 当前没有 F4 production 资产、accepted view evidence 或真实标定样本；13 项继续保持 F2 |
| M18 指标相关关节质量门禁 | completed_at_F2_safety_level | `primary-player-v0.3.0` 输出逐关节跳点和逐左右关节对交换证据；quality v1.3 以 required feature 的关节依赖做交集，只将无关诊断降级为可追溯告警，相关诊断与 legacy/缺 provenance 继续 fail-closed；v0.7 真实产物重算 | 跳点/交换候选本身仍需人工关键点真值评测 precision/recall；不构成 Pose 准确率结论 |
| M19 并发评分阻断完整归因 | completed_at_contract_level | `minimum-scoring-loop-v0.4.0` 在特征缺失或 hard fail 已使结果不可用时，仍保留身份、跳变、左右交换、目标方向等全部并发 score-only 原因；Bundle validator 强制 raw block flags 不得从 reason codes 消失；三套真实产物、报告和不可变空标定数据集重建 | 原因完整不等于诊断准确；每类候选仍需人工真值评测 |
| M20 Pose 诊断视频复核队列 | tooling_completed_review_pending | 跳点、左右交换、身份歧义和 source Track 切换按源帧/关节跨重叠事件去重；连续动态视频精确 seek、受影响指标、相邻帧坐标、CSV 导出/校验和 SHA lineage；主报告直接链接三套真实队列 | 任务仍全部 pending；人工决定未独立裁决，不能自动修改质量门禁 |
| M21 边界受限阶段代理 | completed_at_F2_safety_level | phase v0.2 对右边界截断的下行活动保留实测峰、对仅两帧的减速尾部保留低样本峰、对左右脚峰接近保留歧义侧代理；quality v1.4 只允许 F2 测量并阻断依赖指标的 A～E | 代理阶段仍需人工边界误差评测；单帧尾部继续 unavailable |
| M22 评分阻断精确解释 | completed_at_contract_level | `minimum-scoring-loop-v0.4.1` 将身份、关键点跳变、左右点分配、目标方向、右边界截断阶段、两样本阶段和启动脚侧别歧义分别映射为稳定 reason code/反馈；同窗机器对照和主报告按指标×事件实例展示多标签阻断计数 | reason 只说明为何 fail-closed，不证明诊断本身准确；仍需视频人工复核 |
| M23 Pose 诊断全时间线真值评测 | tooling_completed_annotation_pending | `pose-diagnostic-truth-pack-v1.0.0` 将完整覆盖区间与稀疏真阳性分离；exact frame + joint/pair 评测 TP/FP/FN、precision/recall/F1；Halpe/WholeBody 同窗各有 600 帧浏览器盲审入口 | 两包 accepted coverage/positive 均为 0；无独立人工裁决时所有指标保持 null，门禁不调整 |
| M24 Pose 诊断接受协议与策略审核 | contract_completed_protocol_and_truth_pending | 外部预注册接受协议精确绑定范围与来源哈希；审查器从 coverage/positives 重算 TP/FP/FN 和指标，按原始计数汇总；满足条件也只进入人工策略审核 | 没有外部登记协议和全时间线人工真值；当前报告为 `annotation_and_protocol_required`，不会自动改变当前 quality v1.6 |
| M28 右边界减速阶段确认 | completed_at_F2_safety_level | event v0.4.1 / phase v0.3 仅在事件右边界速度峰后存在两个时间戳有效、严格递减的实测样本时保留边界峰；后续样本只作确认且不进入事件特征；全片 feature measured 由 400 提升到 401 | 该阶段仍是右截断运动学代理，正式评分继续阻断；其余 41 条按真实特征/质量门禁保持 unavailable |
| M29 主球员 Pose 覆盖双门禁 | completed_at_F2_safety_level | quality v1.6 将事件级 `primary_pose_coverage_low` 从测量 hard fail 改为 score-only block；required features 自身完整时保留 F2 测量，全片 feature measured 由 401 提升到 403 | 低 Pose 覆盖仍禁止 A～E；Track 覆盖不足、confirmed ID switch、必需阶段或 required feature 缺失继续硬失败 |
| M30 全片 Pose 配置路由审计 | completed_without_model_switch | 同一 2,911 帧、同一 Halpe256 候选边界比较 Halpe256/Halpe384/WholeBody133；保留 Halpe256 主配置并禁止逐事件 cherry-picking | 没有人工关键点真值，不能形成模型准确率排名 |
| M31 评分阻断互斥审计 | completed | 442 条指标实例拆为 176 calibration、227 feature-complete score-only blocked、3 feature-only incomplete、33 双重阻断、3 hard-fail | 诊断候选 precision/recall 与教练标定仍缺失 |
| M32 视频可定位真值工作清单 | tooling_completed_annotation_pending | 129 个 event×flag 工作项覆盖全部 227 条完整特征评分阻断，连续视频 seek、JSON/CSV 和诊断队列关联 | accepted annotations 仍为 0 |
| M33 特征观测缺口审计 | completed | 将 39 条 feature unavailable 定位到 11 个事件、138 个唯一 event×feature 缺口，并做固定边界替代模型审计 | 仍需人工关键点/事件真值判断可恢复性与误差 |
| M34 小 ROI Pose 恢复实验 | experiment_completed_production_unchanged | 同一视频/检测/Track/模型/事件边界，仅把 Pose 最小 ROI 32px 改为 8px；131/131 目标帧恢复 Pose，固定边界恢复 20 条特征完整指标实例、0 回归；13 秒 H.264 动态 A/B | 远场小框关键点没有人工误差真值；生产默认继续 32px，不启用自动 fallback |
| M35 小 ROI 关键点独立真值与误差接口 | tooling_completed_annotation_pending | 131 个恢复帧冻结为 1,834 个关节点任务；双人独立盲标、第三方裁决、像素与 bbox 尺度 MAE/P95/Bias/有效率评测、分关节/分框尺寸结果 | 人工标注与裁决均为 0；误差字段为 null，生产 32px 和 F2 状态不变 |
| M36 13 项可计算性证书与残余不可用分类 | completed_at_F2_observability_level | 逐项验证 required feature 的值、单位、版本、置信度和证据帧；13/13 均有真实 measured 实例；423/442 measured；19 条残余以动态视频证明为画面裁切、Track 过渡或视频起始截断 | 只证明合格观测上的可计算性；事件/关键点真值和 A～E 标定仍为 0，19 条继续 fail closed |
| M37 正常上传默认 Pose 替换 | completed_at_F2_deployment_level | Service/Worker 默认改为 RTMPose-M Halpe26，YOLO Pose 只作显式回滚；真实 GPU Worker 与模型 SHA、注册表和 bundle 验证闭合 | 默认模型替换不等于准确率或 F3/F4；正式 A～E 仍被真值与标定门禁阻断 |
| M38 每次上传的逐指标计算就绪报告 | completed_at_F2_reporting_level | Pipeline 自动生成 `calculation-readiness.json`，逐项区分候选缺失、特征不可测和评分证据阻断；短烟测 11/13、全片 13/13 至少有一个 measured 候选 | 短片不保证包含每类完整动作；报告不放宽门禁，也不把可计算性当作事件/等级准确率 |
| M39 每视频逐指标代表性测量组合 | completed_at_F2_delivery_level | Pipeline 自动生成 `indicator-measurement-portfolio.json`，从每项 measured 候选中按观测质量确定性选择一个完整特征向量；短烟测 11/13、全片 13/13 | 代表实例不是“最好动作”；无真值时仍不输出 A～E，短片缺项继续 unavailable |
| M40 同一动作周期的 13 项闭合测量 | completed_at_F2_cycle_level | Pipeline 自动生成 `scoring-cycle-measurement.json`，用 initiation/peak phase 唯一绑定同一 Track 的 FS01→FS02→FS09；全片 34 个周期中 25 个为同周期 13/13 | 周期仍是规则候选，不是事件真值；短片最佳 11/13，不允许跨动作拼接 |
| M41 FS02-M02 目标方向参考上下文 | tooling_completed_reference_pending | 版本化参考上下文、视频 SHA/事件边界绑定、独立复核、角度误差计算、Pipeline/CLI/报告接线；全片 34 个待填写工作项 | 当前 accepted=0；不得从移动方向推断战术目标，FS02-M02 继续 unavailable |
| M42 测量向量与评分向量分离 | completed_at_contract_and_runtime_level | registry 显式区分 `measurement_features` 与完整 `required_features`；FS02-M02 将目标方向对齐误差纳入标定/评分向量；manual truth、compiler、独立测试、batch、validator、报告与真实 GPU/全片产物闭合 | 34 个全片目标方向和 3 个烟测目标方向仍 pending；人工真值/标定/独立测试仍为 0 |
| M43 类型化阻断审计与上下文特征误差 | completed_at_evaluation_interface_level | score reason 对 raw gate 做双向一致性审计；目标方向对齐误差接入人工 semantic、关键点和事件边界驱动的 MAE/P95/Bias/分视角/四类误差预算；空真值报告和主报告重建 | accepted target semantic、人工事件和关键点仍为 0；目标方向误差为 null，13 项仍为 F2 |
| M44 当前全量真值行动路由 | tooling_completed_annotation_pending | v2 清单绑定当前 scores/events/summary/audit/registry/Pose queue/目标方向文件/视频；266/266 unavailable 覆盖为 168 个视频任务和 481 条实例×动作关联 | 全部任务仍 annotation_required；清单不替代人工标注、教练标定或独立测试 |
| M45 人工行动证据完成度状态机 | tooling_completed_annotation_pending | 从 M44 清单与原始事件、关键点、语义、Pose 诊断和目标方向证据重算逐任务/逐实例状态；拒绝自报完成或篡改评测结果 | 当前 168/168 工作项及 266/266 指标实例仍为 annotation_required；人工 accepted evidence 仍为 0 |
| M46 共享证据获取计划 | tooling_completed_annotation_pending | 将 168 个 work item 的重复证据依赖折叠为 87 个 hash-bound 单元；完整时间线 Pose、事件边界、阶段、关键点、特征、侧别和目标方向分层排序 | 87/87 单元仍 annotation_required；优先级不是准确率或人工工时估计，完成后仍须重跑评分 |
| M47 单命令不可变真值刷新 | tooling_completed_annotation_pending | 预检 CSV 后快照 compiled truth、Pose truth、registry、worklist、目标方向；同一 bundle 内重算事件/特征误差、Pose 误差、M45/M46，最后原子更新 latest 并刷新总报告 | 当前 `m47-empty-v2` 仍为 0 人工真值、0/168 work item、0/87 evidence unit；不会运行 A～E 评分 |
| M48 真值刷新到标定数据集的不可变交接 | tooling_completed_annotation_pending | 从 M47 冻结人工事件按原 event_id 重算 manual-event features，精确编译 13 项 prepared dataset，并逐项执行拟合前门禁；交接 manifest、latest、Schema 与总报告均校验来源 SHA | 当前 0 人工事件、0 feature、0 sample、0/13 fit-ready；13/13 prepared 仅结构有效，不生成候选、阈值、模型、等级或 F3/F4 |
| M49 三视频统一测量源与标定组合 | tooling_completed_annotation_pending | 真值包 3/3 视频共 15,868 帧既有 RTMPose-M/Halpe26 结果统一重建 `primary-player-v0.3.0`；逐视频人工边界特征再汇总到同一 13 项 calibration dataset，来源/版本/SHA 可重放 | 人工事件、特征、sample 仍为 0/0/0，0/13 fit-ready；复用已有 Pose 帧，未运行 GPU、候选事件检测、拟合或评分 |
| M50 三视频 13 项候选事件计算覆盖 | completed_at_F2_observability_level | 复用同一 3 段、15,868 帧当前 Pose/主球员输入，无 GPU 重推理；546 个候选事件、2,366 条指标实例中 2,276 条特征向量 measured，13/13 指标在每段视频均至少有一条完整可追溯向量 | 候选事件尚无人工真值；Event/feature accuracy 未知，评分仍为 828 calibration_required / 1,538 unavailable，grade/threshold 均为 0 |
| M51 计算完整性与评分就绪互斥拆解 | completed_at_contract_and_audit_level | 从 M50 原始 measurement/scoring vectors 与 quality gate 重放 2,366 条实例：18 measurement hard fail、72 测量向量不完整、180 评分上下文缺失、1,268 评分证据阻断、828 仅缺标定/独立测试 | 不解除任何门禁、不补 0；候选事件与诊断仍无人工真值，828 只表示可进入标定应用而非已评分 |
| M52 未观察到净制动不得冒充缺测 | completed_at_F2_feature_semantics_level | FS09 v0.2 在双脚净速度下降均不为正、但存在局部减速峰时，用较强局部峰确定运动学候选侧并保留负/零净变化；14 条真实事件由 unavailable 恢复为 measured，真正缺样本仍 fail closed | 只证明代理特征语义完整，不证明制动脚真实触地、事件准确率、动作等级或正式评分 |
| M53 未观察到正向膝伸展不得冒充缺测 | completed_at_F2_feature_semantics_level | FS01/FS02 v0.5 在双膝完整可见但峰值伸展速度均不为正时，保留较强的非正伸展值和实际时序作为负向运动学证据；1 条真实 FS02-M03 从 unavailable 恢复为 measured | 候选侧不等于真实支撑脚，负值不代表力/功率；事件、关键点与等级真值仍缺失 |
| M54 平滑反事实可重建性审计 | completed_at_evaluation_coverage_level | 修正 FS09 v0.2 误差评测对“净下降非正、局部减速选侧”的旧重放逻辑；新增 51 个 Pose required feature、三视频 SHA/registry 绑定覆盖报告与严格 Schema | 仅 27/51 特征的当前 raw payload 可全量无歧义重放；人工关键点/事件真值仍为 0，覆盖率不是特征准确率 |
| M55 FS02-M03 平滑反事实精确回放 | completed_at_evaluation_sensitivity_level | 复用生产侧别/测量侧纯函数，从 raw 膝屈曲与足部垂直速度按 `timestamp_ms` 重放侧别、膝伸展速度和时差；审计契约 v1.1 新增逐特征敏感性统计与来源重放 | 髋加速度方向投影仍缺 raw 方向原语；反事实差异是平滑敏感性，不是 Pose/特征准确率或等级差异 |
| M56 FS02-M02 启动方向平滑反事实 | completed_at_core_indicator_error_budget_level | 从 raw 髋中心二维轨迹复用生产方向纯函数，182/182 启动方向可重放；v1.2 按跨 ±180° 的环形最短差统计敏感性，并接通目标方向对齐误差的 smoothing budget | 人工目标方向和校正关键点仍为空；160.52° 最大差异只标记优先复核事件，不能判断 raw/smoothed 孰优 |
| M57 FS09 稳定持续时间证据合同 | completed_at_core_indicator_error_budget_level | `stability_duration_ms` 生产与反事实统一复用 timestamp-aware 纯函数；raw/smoothed 速度与肩髋角速度序列完整入账，177/177 有效实例可重放；三视频当前覆盖与就绪度重绑定 M57 | 34.05/124.2 ms mean/P95 只表示平滑敏感性；人工关键点、事件边界和等级差异仍为空，不能判定哪条链更准确或晋级 F3 |
| M58 FS02 启动方向加速度证据合同 | completed_at_core_indicator_error_budget_level | `hip_acceleration_along_launch_direction_body_s2` 生产与反事实统一复用 raw/smoothed 二维髋轨迹、实际时间戳和 body-normalized 加速度纯函数；182/182 有效实例可重放 | 最大平滑差 9,311.44 body/s² 暴露二阶导数高敏感性；没有人工关键点真值时不能选择 raw/smoothed、生成阈值或推进 F3 |
| M79 跳点/左右交换动态双盲真值 | tooling_completed_annotation_pending | 三视频 1,371 个 score-relevant 候选冻结为 103 个独立 H.264 窗口；两名 annotator 盲审原始记录与第三名 reviewer 裁决完全分离；Range 服务支持片段/逐帧定位；队列重放拒绝重哈希伪造 | coverage/positive/adjudication 仍为 0/0/0；候选窗口只能评测 precision，不能测完整时间线 recall/F1，也不改 gate 或 F2 |

当前权威注册表 `metric-feasibility-pose-wave-v2.json`（`pose-wave-2026-08-22.17`）中 13 项均为 `F2`：FS01-M02/M03/M04/M05、FS02-M02/M03/M04/M05、FS09-M01/M02/M03/M04/M05。F2 仅表示可在候选事件上生成版本化测量，不表示事件准确、特征可区分或已可评分。M42 将 `required_features` 定义为完整标定/评分向量，并新增可选 `measurement_features` 作为 Pose/传感器独立可测子集；未声明时两者相同。FS02-M02 的 Pose 子集为 4 项，完整评分向量为 5 项，新增 `target_direction_alignment_error_deg`。97 秒全片 102 个候选事件、442 条指标实例中，Pose 测量层 403 条 measured/39 条 unavailable，评分状态 176 条 `calibration_required` / 266 条 `unavailable`；34 个 FS02-M02 均因目标方向 pending 而保持完整评分向量不可用。当前 GPU 120 帧产物仍是 `.15` 的历史部署烟测；M53 使用相同已存 Pose/Track 做无 GPU 后处理重算。历史 31–51 秒 Halpe/WholeBody 同窗只保留为模型比较快照，不冒充 `.17` 标定输入。全部结果 grade=0、threshold=0；事件数量、特征可测率和评分门禁计数都不得解释为准确率。

历史 v0.4.1 只修正当时的阻断解释；其同窗/全片 reason 计数保留为历史快照。当前 `.15` / loop v0.6.0 必须使用 M43 双向审计：全片类型化原因中 identity/jump/swap/target 分别为 43/201/69/34 条，每一条 reason 都有对应活动 raw flag，反之亦然。计数允许同一指标实例并发命中，不能相加当作失败总数。

M34 没有改写上述权威生产计数。独立实验仅在固定相同 102 个候选事件上表明：若将 32px 最小 ROI 放宽为 8px，特征完整实例可由 403/442 变为 423/442；该数值只属于 `experimental_observability_only_not_production`，不能与当前正式 bundle 混写，也不能据此更新 F2/F3/F4 或 A～E 状态。

M35 把 M34 的全部 131 个恢复帧冻结为 1,834 个肩、髋、膝、踝、脚趾和脚跟关节点任务。浏览器工作台只显示无模型骨架的原视频和人工点，不嵌入或预填模型坐标；每个任务必须由两名不同标注者独立完成，再由未参与标注的 reviewer 裁决。当前 raw annotation=0、accepted adjudication=0，因此关键点 MAE/P95/Bias/valid rate、分关节和分 bbox 四分位结果全部保持 null；PCK 也不会在没有外部预注册阈值时计算。该接口不会自动启用 8px、修改 quality/scoring gate 或推进成熟度。

M36 对实验固定边界的 442 条指标实例建立可计算性证书：13/13 个注册表指标都至少有一个 required-feature 契约完整、值非 null、单位/版本/置信度和证据帧齐全的真实实例，每项有 30～34 条 measured，总计 423/442。剩余 19 条集中在 6 个候选事件，其中 12 条球员实际越出画面边界、6 条跨 source Track、1 条缺少视频开始前的上下文；4.77 秒 H.264 动态证据逐帧展示三类原因。系统没有用 0 填充、降低质量门禁、放大已出画面的身体或跨模型挑值，19 条继续为 `unavailable`。这证明 13 项在合格观测上都能计算，不证明候选事件准确、特征真值准确、F3/F4 或 A～E 可评分。

最初六项的冻结三视频结果——591 个候选事件、1,182 条指标记录、1,132 条 `calibration_required`、50 条 `unavailable`——以及早期 120 帧 YOLO/RTMPose 烟测仅保留为历史可复现基线，不代表当前 13 项同窗结果。

> 本计划不创建或推测 A～E 阈值。没有教练真值、特征误差结果和独立测试时，评分输出只能是 `calibration_required` 或 `unavailable`。文中所有尚未有真值支持的性能门槛均记为 `null / pending_protocol_registration`，不能在看过测试结果后倒推门槛。

当前 M14 已把工程路径完整接通但没有伪造数据：`review.html` 可人工录入事件/phase、逐帧关键点和 51 条语义任务；`build_manual_event_features.py` 只在 accepted 人工边界上重算 13 项并禁止候选 ID 模糊匹配；`compile_calibration_dataset.py` 以人工 ID 精确关联并按球员/场次/视频连通分量封存 independent test；fit 和 test 的所有数值门槛必须来自各自预注册协议；promotion 还要求显式人工决定和注册表已经为 F4。实际空包编译为 `annotation_required`，13 个 prepared 文件均不可拟合，真实晋级在 F2 被硬拒绝且不落资产。

M15 将“工程接口存在”与“科学成熟度已通过”彻底分离。production promotion 现在必须绑定机器可读的 F0→F4 maturity-evidence：F1 绑定人工事件评测，F2 绑定人工关键点下的 MAE/P95/Bias、分视角结果和误差预算，F3 绑定教练一致性、等级区分度与内部验证，F4 绑定封存独立测试；任一阶段缺失、跳级、注册表哈希不一致或 required event/feature 集合缩水均拒绝。全 13 项 threshold/ordinal 路径只以 `synthetic_test_only` 数据完成契约烟测，不是阈值、效果或生产可用性证据。

M16 进一步消除了几类“能跑但不可追溯”的假阳性：静止 Pose 和任意幅度的共模画面抖动不再被自适应分位数制造为 FS01/FS02/FS09；主球员选择改为确定性时序优化并输出选择分数间隔与歧义帧；实际 `primary-player.jsonl` 的算法版本必须逐行一致且与 registry 精确匹配，不能由 Summary 用当前常量冒充。当前全片使用 `primary-player-v0.3.0` 选择一次，再将 930–1529 帧切片作为两模型共享同窗；它与 v0.2 除版本字段外逐行选择结果 0 差异。600/600 帧的检测、Track 和 bbox 已逐行一致，Pose 输出保持各模型独立。v0.3 新增逐关节跳点/交换证据；当前 quality v1.6 延续 v1.3 的关节交集门禁、v1.4 的阶段回退指标级阻断，以及 v1.5/v1.6 的测量/评分双门禁。无交集时保留 `outside_indicator_joints` 告警，旧/不完整 provenance 则保持原严格门禁。`features.jsonl`、`indicator-features.jsonl`、`scores.jsonl` 和 Summary 继续做内容级关联与 SHA-256 绑定，联动篡改或跨层数值不一致会被运行产物校验拒绝。

教练只提供相对排序时，系统现有独立 `calibration_ranking` 路径可编译逐教练成对偏好、按 leakage group 拆分、拟合 Bradley–Terry 相对排序并封存独立测试。该路径永远返回 `relative_order_only`，`grade/confidence/threshold_version=null`，也不能进入 A～E production promotion；这满足“支持排序标注”的接口要求，但不会把相对顺序伪装成等级阈值。

模型替换后的事实分两层记录：历史六项 A/B 中，RTMPose-M 256 的端到端 unavailable 为 28/1,218（2.30%），YOLO 为 50/1,182（4.23%）；固定同一 591 个历史事件区间后，RTMPose-M 384 为 14/1,182（1.18%）。这只比较候选事件上的特征可测率。当前 13 项结论以 `reports/fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json` 为准。Halpe26/WholeBody133 的细足点允许构造足部运动学代理，但不能直接观测离地、落地、地面接触、支撑力或真实压力中心；任何覆盖率改善都不是准确率或 F3/F4 证据。

用户可见状态已经改为两层：`measurement_status` 回答“事件实例的特征是否测得”，`scoring_status` 回答“是否有资格输出正式等级”。13 项注册表成熟度均为 F2，但单个事件实例仍可因质量门禁成为 `unavailable`；有效实例在没有获批标定资产时只能是 `calibration_required`、`grade=null`。这代表闭环已跑通到测量层，不代表 0 分，也不代表已经具备正式 A～E 评分能力。

最终验收审计补强后，标定资产不再因“Schema 合法”或自声明 `passed=true` 就能输出等级。生产资产必须使用 1.2.0 契约，绑定精确特征单位/版本，并携带完整 `promotion_lineage`；其 canonical SHA、lineage 和晋级报告还必须匹配由部署运维选择的版本化可信晋级账本。账本路径不能由普通任务请求指定，也不能放入服务的 `uploads`、`requests` 或 `runs`。独立测试评测会从 seal 内原始多教练标签重新计算一致结论，不信任可单独伪造的 summary。信任链缺失、未通过或 test-only 资产默认保持 `calibration_required`。`scripts/score_calibrated_indicators.py` 可直接消费真实运行的 `indicator-features.jsonl`，但生产资产同样要求显式可信账本，且该脚本从不训练模型或推导阈值。`reports/scoring-loop-completion-audit.json` 是历史六项验收映射；当前 13 项必须联合 v2 注册表、measurement plan、真值包 manifest 和同窗比较 JSON 审计。

真值链路审计又修复了一项重要风险：旧的 `apply_keypoint_corrections` 会保留未人工标注帧的模型坐标，使“人工真值特征”掺入模型预测并低估 Pose 误差。现在真值序列只保留显式人工标注点，未标注帧为缺失；同一帧/关节的多标注者结果必须先裁决，不能按 JSONL 顺序静默覆盖。稀疏真值不足时报告 `insufficient_keypoint_ground_truth_coverage`，不能显示为已评测。

## 1. 审计结论

> 本节 1.1～1.4 记录的是 2026-08-12 接手时的仓库快照；其中“当前/现有/没有”均以该审计时点为准。实施后的权威状态见第 0 节和 M1～M16，不应把本节摘录为 2026-08-13 的运行现状。

### 1.1 已核对的仓库事实

- 已完整阅读 `docs/RallyMate推理评分系统技术设计与维护手册_v1.0.md`，并对照核心代码、测试、公开 JSON Schema、评分卡注册表、SQLite 任务记录和真实运行产物。
- 当前 Git 仓库没有提交，工作树中的代码、模型、视频和产物均为未跟踪内容。实施时必须保留用户现有文件，不以 Git 回滚或覆盖方式处理。
- 当前链路确已具备视频上传、SQLite 持久任务、常驻 GPU Worker、Detect、ROI Pose、基础 Tracking、逐帧 `frames.jsonl`、标注视频、静态评分颗粒度审计和 HTML 报告。
- `src/rallymate_scoring/data/metric_cards.json` 含 298 张卡，注册表版本为 `2026-08-10-demo.1`；静态结果为 `supported=117 / partial=75 / unsupported=106`，但所有 `score_ready=false`。
- 在 2026-08-12 初始审计时，本轮最先指定的六项均为 Pose-only，COCO-17 可提供其显式要求的肩、髋、膝、踝点；这只证明当时“结构可支持”，不能证明特征准确或可评分。当前范围已由 v2 注册表扩展到 13 项，其中细足运动学代理使用 Halpe26/WholeBody133 足部参考点。
- 存在一个需版本化处理的语义差异：当前评分卡把 `FS02-M02` 命名为“重心向来球方向转换”，本轮需求使用“重心向目标方向转换”。本计划按更保守的 `target_direction` 外部真值设计；不静默覆盖原评分卡，后续在 feasibility 注册表中记录该语义别名和来源版本。
- 当前模型文件哈希与手册一致：
  - `yolo26n.pt`: `9B09CC8BF347F0FC8A5F7657480587F25DB09B34BF33B0652110FB03A8AD4FEF`
  - `yolo26n-pose.pt`: `EB3BB8268828AEAF515CEC23A4BFAFD793944A86FE9AF94BA7823609C14522A9`
- 现有公开 Schema 只有 request、frame observation 和 summary；运行时主要使用 Python 自定义校验，尚无 event、feature、ground truth、calibration 或 indicator score 契约。

### 1.2 主球员与时序现状

- `Yolo26Perception.estimate_poses()` 每帧按检测框面积选择前 `max_players` 个 ROI。面积最大不是主球员身份规则。
- `inspect_frame_observations()` 将全视频出现帧数最多的 Pose Track 称为 `primary_pose_track_id`，没有身份重连、事件内门禁或人工主球员真值。
- 基础 Tracker 仅使用 IoU 和中心距离贪心匹配，没有运动状态、外观 Re-ID、全局匹配、身份置信度或遮挡重连。
- 真实长任务 `6d6bd08a-6cfd-4cdf-b15c-77db7627520b`：
  - 11,516 帧，Player 帧覆盖 99.97%，Pose 帧覆盖 98.30%；
  - 最长 Pose Track 仅 42.09%，Pose Track 共 33 条；
  - 当前逐帧最大框身份在该任务中发生 59 次切换；
  - 多个长 Track 依次覆盖视频不同区段，说明同一业务主球员很可能被拆成多个原始 Track；
  - Ball 最长 Track 2.65%，Racket 最长 Track 1.11%，但二者不属于本轮评分范围。

### 1.3 初始审计时点的事件、特征、真值和评分现状

- 没有 `events.jsonl`，没有 FS01/FS02/FS09 事件定位器，也没有人工事件标注导入和 Event F1、Segment IoU、Boundary MAE 评测。
- 没有统一、版本化、纯函数的 Pose 特征库；速度/加速度、非均匀时间戳、缺失值、平滑和证据帧尚未形成代码契约。
- 没有人工校正关键点导入、特征 MAE/P95/Bias/有效率或分视角报告。
- 没有特征误差预算，不能区分 Pose、事件边界、平滑和缺失对误差的贡献。
- 没有教练标签 Schema、标注者一致性、阈值版本或序数模型接口。
- 当时 HTML 报告只展示 298 项结构/覆盖审计，不展示事件时间轴、指标成熟度、特征结果或误差评测。

### 1.4 测试与产物基线

- 2026-08-12 已运行 `scripts/run_tests.ps1`：28/28 通过，耗时 0.692 秒。
- 已用现有校验器复检上述 11,516 帧真实任务：11,516 帧、30,513 个检测、17,107 个 Pose 均通过当前一致性校验。
- 当前真实任务仍为 `score_ready=0 / score_blocked=298`，且不存在 event、feature 或 indicator-score 产物。
- 现有测试能证明工程契约和“不伪造评分”，不能证明主球员身份、事件准确率、特征误差或教练评分一致性。

## 2. 本轮边界与默认决策

### 2.1 只做以下闭环

```text
frames.jsonl
  -> 主球员候选路径和原始 Track 映射
  -> Pose 时序质量诊断
  -> FS01 / FS02 / FS09 候选事件
  -> 当前 13 指标所需的版本化 Pose-only 特征
  -> 事件/特征真值评测与误差预算
  -> 标定接口（无真值时 calibration_required）
  -> 可解释报告和证据追溯
```

### 2.2 明确不做

- 不增加 298 项静态覆盖，不扩展当前 13 项之外的其他 285 项。
- 不开发或修改 Ball、Racket、Court 评分分支。
- 不自定义扩展关键点拓扑，不先做 Keypoint Refinement；细足点仅消费 Halpe26/WholeBody133 的原生输出。
- 不训练大型 Transformer；第一版事件层只做可解释规则/轻量时序基线。
- 不设计总分，不把帧覆盖率写成准确率。
- 不做大规模页面、API、数据库或部署重构。
- 不把事件/QC 规则参数冒充 A～E 评分阈值；两类版本必须严格分开。

### 2.3 默认技术决策

- 保留现有 Detect + ROI Pose 和原始 `frames.jsonl` 契约，不回写或篡改原始 Track ID。
- 新增一个派生的稳定主球员身份 `primary_player_id`，并保留它到原始 `person_track_id` 的逐段映射。事件和特征引用稳定身份，同时证据中列出所有原始 Track ID。
- 特征层只使用每帧的 `timestamp_ms` 求导，绝不从固定 FPS 推导 `dt`。当前源时间戳由 `frame_index / fps` 生成这一事实会写入 provenance；若输入时间基准不可信，时序特征可被门禁为 unavailable。
- 所有空值在内存中使用显式 validity mask/`None`，JSON 中使用 `null`；禁止用 0 代替缺失。
- `confidence` 分层保存。事件规则置信度只称为 heuristic confidence，未经真值校准前不能解释为准确概率。

## 3. F0～F4 可评分成熟度模型

现有 `supported / partial / unsupported` 保留，继续表达“观测结构支持程度”；新增 F0～F4 只表达当前 13 个指标的评分闭环成熟度。两者不得互相覆盖。

| 等级 | 定义 | 晋级所需证据 | 未满足时的输出 |
|---|---|---|---|
| F0 | 结构可支持 | 指标、事件、点位、视角、特征公式和 provenance 契约已冻结；COCO-17 几何语义通过审查 | `unavailable` 或研发态 feature candidate |
| F1 | 事件可定位 | 可输出合法候选区间；人工事件标注可导入；Event F1、Segment IoU、Boundary MAE 可计算；在预注册协议上达到目标 | 未有事件真值时保持 F0，并记录 `event_ground_truth_missing` |
| F2 | 特征可测量 | 特征纯函数、单位、缺失/平滑规则、证据帧冻结并能在候选事件上输出；人工真值评测接口可执行 | 评分为 `calibration_required`；误差未验证不得晋级 F3 |
| F3 | 特征可区分且可标定 | 教练多标注者数据、等级/排序区分度、标注一致性、校准版本和内部验证均通过 | `calibration_required` |
| F4 | 通过独立测试后正式可评分 | 完全独立的球员/场次/机位测试集通过，置信度和 unavailable 门禁经验证，版本已冻结 | 未通过时回到相应较低等级，不输出正式分 |

成熟度不是由代码存在自动晋级。每次晋级必须在注册表中写入 `evidence_refs`、评测集版本、协议版本和审批记录；目标值必须在查看评测结果前登记。

### 3.1 机器可读注册表

注册表产物：

- `contracts/metric-feasibility.schema.json`
- `metric-feasibility.json`（初始六项历史注册表）
- `metric-feasibility-pose-wave-v2.json`（当前权威 13 项注册表）
- `src/rallymate_scoring/feasibility.py`

当前 v2 注册表只包含本轮 13 项，不扩到 298 项；`metric-measurement-plans.json` 从该注册表派生 `current_f2_indicator_ids`，不得另行硬编码数量。每项至少包含：

```json
{
  "indicator_id": "FS01-M02",
  "feasibility_level": "F0",
  "required_events": ["FS01"],
  "required_features": ["hip_center_y_body", "left_knee_flexion_deg"],
  "view_constraints": ["fixed_camera", "single_primary_player", "coco17"],
  "ground_truth_requirements": {
    "event_annotations": true,
    "corrected_keypoints": true,
    "coach_labels": true,
    "independent_test": true
  },
  "current_blockers": ["event_ground_truth_missing", "feature_error_unmeasured", "coach_calibration_missing"],
  "acceptance_metrics": [
    {
      "name": "event_f1",
      "target": null,
      "target_status": "pending_protocol_registration",
      "required_for": "F1"
    }
  ],
  "versions": {
    "registry_version": "0.1.0",
    "metric_card_registry": "2026-08-10-demo.1",
    "event_contract": "1.0.0",
    "feature_contract": "1.0.0"
  },
  "evidence_refs": []
}
```

最初六项以及后续扩展项在实现前均从 F0 开始；当前 13 项完成候选事件和版本化特征闭环后登记为 F2。F2 只表示“可测量”，不表示准确、可区分或可评分；F3 必须有真实误差、教练区分度与标定证据，F4 必须通过独立测试。

## 4. 稳定主球员时序输入

### 4.1 两遍式、非侵入式设计

第一遍继续生成原始 `frames.jsonl`；第二遍读取所有 Player/Pose 候选，构建稳定主球员路径：

1. 为每个原始 Track 统计持续时间、时间间隙、Pose 有效率、检测置信度、身体尺度连续性和运动连续性。
2. 使用全时序动态规划/最小代价路径选择主球员候选，不再逐帧选最大框。
3. 允许把互不重叠、运动和外观几何连续的原始 Track 片段映射到同一个稳定 `primary_player_id`；第一版不做视觉 Re-ID，只做可解释的几何/时序拼接。
4. 支持人工主球员 seed/区间标注，人工标注优先于自动选择。
5. 不修改原始 Track ID；输出稳定身份与原始 ID 的映射和选择理由。
6. 事件切分后按事件重新计算诊断。事件内存在身份歧义或无法解释的切换时，事件和相关特征门禁为 unavailable。

建议新增：

```text
src/rallymate_vision/primary_player.py
src/rallymate_vision/temporal_diagnostics.py
contracts/primary-player-track.schema.json
```

派生产物：`primary-player-track.jsonl`。核心字段：

```text
schema_version, job_id, primary_player_id, timestamp_ms, frame_index,
source_person_track_id, selection_confidence, selection_reason_codes,
pose_present, mapping_version
```

### 4.2 事件内诊断

每个事件至少输出：

- `track_coverage_fraction`：事件区间内映射到稳定主球员且有 Pose 的时长/采样比例；只作为覆盖，不称为准确率。
- `id_switch_count`：稳定路径在事件内跨越的原始 Track ID 次数；如果有人工身份真值，另输出真值定义的 ID Switch。
- `keypoint_valid_fraction`：按所需关节点和全 17 点分别统计。
- `left_right_swap_count/rate`：根据相邻帧同侧运动代价、骨架长度和躯干方向连续性标记疑似交换；第一版只诊断，不自动改点。
- `keypoint_jump_count/rate`：用 body-scale/s 和非均匀时间戳速度的局部 median/MAD 标记跳点，QC 参数版本化。
- `longest_missing_ms` 和 `longest_missing_frames`：按关键点和整体 Pose 分别报告。
- `quality_flags`：身份歧义、长缺失、左右交换、跳点、低有效率、时间基准异常。

上述 QC 参数是观测质量参数，不是 A～E 阈值；必须有独立版本、来源和验证记录。

## 5. Pose-only 事件层

### 5.1 模块与产物

建议新增：

```text
src/rallymate_events/
  __init__.py
  schemas.py
  annotations.py
  pose_fs.py
  evaluation.py
```

产物：`events.jsonl`。`contracts/events.schema.json` 的每条记录至少包含：

```text
schema_version, job_id, event_id, primary_player_id, person_track_id,
source_person_track_ids, event_code, start_ms, end_ms,
key_phase_times, confidence, confidence_semantics,
boundary_uncertainty_ms, quality_flags,
event_model_version, source_frame_range, provenance
```

其中 `person_track_id` 是派生层的稳定身份 ID，并显式携带 `track_id_namespace=primary_canonical_v1`；`source_person_track_ids` 保证可追到 `frames.jsonl` 原始 Track。`key_phase_times` 是命名时间点对象，不为缺失阶段写 0。

### 5.2 第一版可解释基线

- `FS01` 分腿垫步：使用髋中心下移/回升、双膝屈曲变化、stance width、双踝和身体中心的同步垂直运动，识别 `preload / unweighting_proxy / landing_proxy / ready`。COCO-17 没有足底接触点，因此落地和离地只能标为 proxy，并写质量旗标。
- `FS02` 第一步启动：使用准备状态后的身体中心速度上升、持续位移方向、髋肩中心方向一致性和双踝非对称启动，识别 `initiation / center_commit / first_step_extension`。Pose-only 只能观察实际运动方向；没有人工目标方向时不能判断“方向是否正确”。
- `FS09` 制动急停/稳定：使用事件前身体中心速度、速度下降/减速度、双膝屈曲、髋中心下降、支撑区关系和肩髋角速度，识别 `brake_onset / peak_deceleration / absorption_peak / restabilized`。

规则优先使用变化点、局部极值、相对基线和持续性，不把未经标注验证的固定经验值写成业务标准。规则参数保存在独立 `event-rule-config` 版本中；输出置信度在校准前标记为 `heuristic_score`。

### 5.3 人工事件真值与评测

新增：

- `contracts/event-annotation.schema.json`
- 人工事件导入器和校验器；字段包括视频/任务、主球员、event_code、起止时间、关键阶段时间、边界不确定区间、视角、完整性、标注者、复核者和标注集版本。
- 评测协议必须显式给出事件匹配规则和容忍窗口，不能藏在代码常量中。
- 一对一匹配后计算 Event Precision/Recall/F1、Segment IoU、start/end/关键阶段 Boundary MAE/P95、False events/min，并按 view、player、完整/遮挡分组。
- 无人工真值时报告 `not_evaluated`，不输出 0 冒充性能。

## 6. 版本化 Pose 特征库

### 6.1 目录与共同契约

新增：

```text
src/rallymate_features/
  __init__.py
  coordinates.py
  validity.py
  smoothing.py
  geometry.py
  kinematics.py
  event_features.py
  schemas.py
  evaluation.py
```

每个公开特征函数是无文件 I/O、无全局可变状态、可重复的纯函数。输入包含 COCO-17 序列、`timestamp_ms`、点置信度、稳定身份映射、事件区间、视角元数据和显式配置版本。输出统一为：

```json
{
  "feature_name": "body_center_speed_body_s",
  "feature_version": "1.0.0",
  "event_id": "evt-...",
  "value": null,
  "unit": "body/s",
  "confidence": null,
  "valid": false,
  "reason": "calibration_required.stability_envelope_missing",
  "reason_codes": [],
  "source_frames": [],
  "source_timestamps_ms": [],
  "raw_value": null,
  "smoothed_value": null,
  "aggregation": "...",
  "window_ms": [0, 0],
  "model_versions": {},
  "provenance": {}
}
```

说明：

- `value` 是当前特征规范选定的可报告值；`raw_value` 和 `smoothed_value` 保留同一语义下的原始/平滑结果。
- 无值时三者均为 `null`，不得写 0。
- `confidence=null` 表示尚未校准；不能用模型关键点置信度直接冒充特征置信度。
- 速度和加速度只使用相邻 `timestamp_ms` 的真实差值。
- 短缺失是否可插值由版本化 validity/smoothing 配置决定；跨越长缺失时直接无效，并记录 gap。
- 平滑前后都保留；事件边界附近不得使用会跨边界泄漏的窗口。

### 6.2 坐标和身体尺度

- `hip_center = mean(left_hip, right_hip)`，仅在双髋有效时计算。
- `shoulder_center = mean(left_shoulder, right_shoulder)`，仅在双肩有效时计算。
- `body_center_2d` 第一版定义为双肩和双髋四个锚点的等权几何中心，明确标记为 torso-anchor centroid，不是人体真实质心；若以后改变权重必须提升特征版本。
- `body_scale_ref` 使用事件内有效的 shoulder width、hip width、torso length 的鲁棒统计，并在整个事件内保持同一参考尺度，避免逐帧尺度抖动制造假速度。
- 固定机位仍受透视影响；跨画面深度移动明显时追加 view flag，不能把 body 单位解释为米制。

### 6.3 基础特征与版本化扩展

下表是初始六项闭环冻结的十个基础特征。当前实现还包含 `fs09-pose-proxies-v0.2.0` 与 `fs01-fs02-pose-proxies-v0.4.0` 两组扩展；`FEATURE_DEFINITIONS` 当前共 58 个版本化定义。数量只表示函数契约覆盖，不表示特征误差已合格。

| 特征 | 定义 | 单位 | 主要有效性条件 |
|---|---|---|---|
| `hip_center_y_body` | `(hip_center_y(t) - median(reference_phase_y)) / body_scale_ref`；reference phase 由事件记录显式指定，图像向下为正 | `body` | 双髋有效、尺度有效、参考阶段存在、固定画面无明显移动 |
| `body_center_speed_body_s` | `body_center_2d/body_scale_ref` 对时间的一阶导数的二维模长 | `body/s` | 时间戳递增、中心有效、间隙可接受 |
| `left_knee_flexion_deg` | `180° - angle(left_hip,left_knee,left_ankle)`；0 表示完全伸展代理，值增大表示屈曲增加 | `deg` | 左髋/膝/踝有效，线段非退化 |
| `right_knee_flexion_deg` | 与左侧同义 | `deg` | 右髋/膝/踝有效 |
| `torso_lean_deg` | shoulder center 到 hip center 相对校正后画面竖直方向的有符号角；正负方向写入契约 | `deg` | 双肩/双髋有效、相机 roll 已知或被标记 |
| `stance_width_body` | 双踝二维距离除以 `body_scale_ref` | `body` | 双踝有效；仅是踝点支撑宽度代理 |
| `hip_center_relative_to_ankle_support` | 髋中心在按画面 x 排序的双踝连线上的标量投影；0/1 为两端，区间外表示超出支撑区 | `ratio` | 双髋/双踝有效、踝距非退化 |
| `body_center_deceleration_body_s2` | `-d(||v||)/dt`；正值表示速度大小下降 | `body/s²` | 先平滑位置、时间戳有效、无长缺失 |
| `stability_duration_ms` | 峰值减速后，中心速度、踝支撑漂移和肩髋角速度持续满足版本化 stability envelope 的最长连续时间 | `ms` | 必须有经事件真值注册的 stability envelope；没有时 `valid=false` |
| `shoulder_hip_angular_velocity` | 肩轴与髋轴二维分离角经 180° 周期展开后对时间求导 | `deg/s` | 双肩/双髋有效、线方向可靠、无跨缺失求导 |

为真正支持 FS02 方向语义，还需同范围内增加两个辅助特征：

- `body_center_velocity_xy_body_s`：二维速度向量，`body/s`；
- `movement_heading_deg`：实际移动方向，`deg`。只有人工目标方向存在时才计算 `target_direction_alignment_deg`；否则不得判断“向目标方向是否正确”。

## 7. 13 个指标的事件内特征定义

以下仅冻结“测什么”，不定义 A～E 边界。7.1～7.6 保留最初六项定义；7.7 补充随后纳入 v2 注册表的七项。

### 7.1 FS01-M02 重心预加载

- 事件：FS01，窗口为 `preload` 起点到最低髋中心/最大屈膝代理阶段。
- 主特征：`hip_center_y_body` 的阶段变化量、左右膝屈曲增加量。
- 辅助特征：预加载段 `body_center_speed_body_s` 连续性、`torso_lean_deg` 波动、`stance_width_body`。
- 关键限制：髋中心下降是 2D 代理；若球员同时明显向镜头远近方向移动，门禁或按视角分组。

### 7.2 FS01-M05 重心重新分配并准备启动

- 事件：FS01 末段，并允许关联紧随其后的 FS02。
- 主特征：`hip_center_relative_to_ankle_support`、落地代理后 `body_center_speed_body_s`、左右膝屈曲状态。
- 辅助特征：`stance_width_body`、到后续 FS02 启动的间隔。
- `stability_duration_ms` 在 stability envelope 未经真值注册时为 `calibration_required`，不能用经验低速阈值填值。
- 没有后续 FS02 时仍可报告当前姿态特征，但“准备启动”的完整指标状态为 unavailable。

### 7.3 FS02-M02 重心向目标方向转换

- 事件：FS02，窗口为 `initiation` 到 `center_commit/first_step_extension`。
- 主特征：`body_center_velocity_xy_body_s`、`body_center_speed_body_s`、事件内持续位移和 `movement_heading_deg`。
- 辅助特征：肩中心与髋中心位移方向一致性、双踝启动时序。
- 没有人工目标方向标签时只报告“实际移动方向与持续性”，评分状态为 `calibration_required` 或因语义不足为 `unavailable`，不得把实际移动方向自动等同于正确目标方向。

### 7.4 FS09-M03 下肢吸收身体惯性

- 事件：FS09，窗口为 `brake_onset` 到 `absorption_peak`。
- 主特征：`body_center_deceleration_body_s2`、左右膝屈曲增加量、`hip_center_y_body` 下降量。
- 辅助特征：`torso_lean_deg` 波动和减速峰时序。
- 加速度只在位置平滑、有效时间跨度和缺失门禁通过时输出。

### 7.5 FS09-M04 重心重新稳定

- 事件：FS09，窗口为 `absorption_peak` 到 `restabilized`。
- 主特征：`hip_center_relative_to_ankle_support`、`body_center_speed_body_s`、`shoulder_hip_angular_velocity`。
- 辅助特征：`stance_width_body`、`stability_duration_ms`。
- 支撑区是双踝二维连线代理，不声称真实压力中心或承重。

### 7.6 FS09-M05 身体进入稳定控制状态

- 事件：FS09 末段，并可关联后续 FS02；FS10 不在本轮自动事件范围。
- 主特征：`stability_duration_ms`、低位保持的 `body_center_speed_body_s`、双踝支撑漂移、`shoulder_hip_angular_velocity`。
- 辅助证据：后续 FS02 是否能连续衔接；若只有 FS10 才能证明后续状态，则记录为未覆盖，不扩展 FS10 实现。
- 视频在稳定形成前结束时输出 unavailable。

### 7.7 v2 扩展七项

| 指标 | 事件内测量定义与主要单位 | 必需真值与语义边界 |
|---|---|---|
| FS09-M01 判断身体惯性方向 | 髋中心速度 `body/s`、图像平面运动方向 `deg`、髋相对双踝中点偏移 `body`、速度趋势 `body/s²` | 需人工 FS09 边界、惯性方向标签、校正肩/髋/踝点和相机运动审计；二维方向不等于球场方向 |
| FS09-M02 制动脚落地 | 左右踝速度/速度下降 `body/s`、制动侧代码、膝屈曲变化 `deg`、髋减速度 `body/s²`、踝减速到髋减速峰时差 `ms` | 需人工制动侧、接触时刻和校正下肢点；当前只能观测踝减速代理，不能确认地面接触或受力 |
| FS01-M03 双脚轻微离地 | 双足参考点上移量 `body`、同步差 `ms`、上移代理持续时间 `ms`、髋垂直速度 `body/s` | 需人工上移阶段、接触标签及校正足/髋点；这是足部上移代理，不是真实离地结论 |
| FS01-M04 双脚分开落地 | 双足垂直减速时差 `ms`、减速后站距 `body`、髋横向波动 `body` | 需人工左右接触/稳定时刻与落地后稳定区间；“垂直减速”不是落地或接触传感 |
| FS02-M03 支撑脚发力 | 图像平面启动方向 `deg`、支撑侧代码、支撑膝伸展速度 `deg/s`、髋沿启动方向加速度 `body/s²`、支撑侧伸展到动足上移代理时差 `ms` | 需人工支撑侧与阶段关键帧；2D 运动学不能测蹬地力、冲量或因果贡献，若将来声称受力必须另有力台/压力真值 |
| FS02-M04 启动脚离地 | 启动侧代码、足部峰值速度 `body/s`、相对位移 `body`、足部运动持续时间 `ms` | 需人工启动侧、运动起点和接触标签；当前只确认足部开始运动，不能确认离地 |
| FS02-M05 第一步落地并建立移动方向 | 足部速度下降 `body/s`、第一步位移 `body`、髋方向一致性 `ratio`、减速到方向形成时差 `ms`、新站距 `body` | 需人工第一步接触/稳定时刻、启动侧和移动方向区间；当前只能观测足部减速/新支撑代理，不能确认落地或战术方向正确性 |

其中本里程碑新增的五项是 FS01-M03/M04 与 FS02-M03/M04/M05。它们全部是固定机位 2D Pose 运动学代理，既不是接触检测，也不是力学测量；必须通过人工事件边界、校正关键点和相应接触/阶段标签量化代理误差后，才可能讨论 F3。

## 8. 特征真值、误差评测和评分误差预算

### 8.1 真值契约

新增：

- `contracts/corrected-keypoints.schema.json`：视频/帧/时间、稳定主球员、COCO-17 修正点、可见性、标注者、复核者、工具和标注集版本。
- `contracts/feature-evaluation.schema.json`：特征、模型值、真值、误差、有效性、视角、数据集和所有版本。
- 人工事件边界使用第 5.3 节契约。

人工修正点也不得用 `[0,0]` 表示缺失；缺失点使用 `null + visibility/reason`。

### 8.2 评测输出

每个特征至少报告：

- MAE；
- P95 absolute error；
- Bias（模型值减人工真值的有符号均值）；
- model valid rate、ground-truth eligible rate 和 paired valid rate；
- 样本数、事件数、球员数；
- 按 `view_id/view_type`、球员、遮挡、远近和事件类型分组；
- 无数据时 `not_evaluated`，不写 0。

### 8.3 误差预算

同一特征函数做配对反事实重算：

```text
P-P-S：预测 Pose + 预测事件 + 生产平滑
G-P-S：人工 Pose + 预测事件 + 生产平滑
P-G-S：预测 Pose + 人工事件 + 生产平滑
G-G-S：人工 Pose + 人工事件 + 生产平滑（参考）
P-P-R：预测 Pose + 预测事件 + raw/no smoothing
G-G-R：人工 Pose + 人工事件 + raw/no smoothing
```

- Pose 影响：比较预测点与人工点替换后的变化。
- 事件边界影响：比较预测边界与人工边界替换后的变化。
- 平滑影响：比较 raw 与生产平滑的变化和偏差。
- 缺失影响：报告有效率损失、因缺失被排除的样本特征分布；只有人工点能补足时才计算数值反事实，绝不用插值伪造真值。
- 交互项不能强行归给某一层，单列 `interaction/residual`；数据足够时可用对称/Shapley 分解降低替换顺序偏差。

只有当特征误差相对潜在等级间差异足够小，且该差异来自教练标定数据时，才讨论 F2→F3。当前不能填写这一比值门槛。

## 9. 评分标定接口

### 9.1 契约与状态

新增：

```text
contracts/coach-annotations.schema.json
contracts/calibration-model.schema.json
contracts/indicator-score.schema.json
src/rallymate_scoring/calibration.py
src/rallymate_scoring/scoring.py
src/rallymate_scoring/backends/threshold_rule.py
src/rallymate_scoring/backends/ordinal_regression.py
```

教练标注支持：

- 单指标 A～E；
- 同指标样本排序/并列；
- 多教练、重复标注、复核状态、适用视角和标注集版本。

一致性报告至少包括：

- A～E：标注分布、两两加权 Kappa、序数 Krippendorff alpha；
- 排序：Kendall 一致性/成对一致率；
- 争议样本和缺失标注明细。

一致性合格门槛由标定协议预注册，本计划不生成数值。

### 9.2 两个后端

- 版本化阈值规则：只加载由教练真值产生并审批的显式有序边界文件；缺文件、版本、样本来源或单调性校验失败时返回 `calibration_required`。仓库不提供默认阈值。
- 序数回归：定义训练数据、特征顺序、缺失策略、模型制品和推理接口；当前无真值时只实现 Schema、数据校验、制品加载/拒绝逻辑和报告，不生成伪模型。

统一结果：

```text
indicator_id, event_id, primary_player_id,
status (scored | calibration_required | unavailable),
grade (A..E | null), confidence (number | null), feature,
threshold_version (string | null), calibration_model_version (string | null),
model_versions, evidence, reason_codes, feedback
```

状态规则：

- 特征/事件/视角质量失败：`unavailable`；
- 特征有效但没有经批准标定：`calibration_required`；
- 只有 F3/F4 所需标定资产存在且门禁通过：`scored`；
- feedback 在未评分时只能解释观测和阻断原因，不能生成等级化教练结论。

## 10. 报告与全链路追溯

### 10.1 新增产物

```text
primary-player-track.jsonl
track-diagnostics.json
events.jsonl
event-evaluation.json
event-features.jsonl
feature-evaluation.json
scoring-error-budget.json
indicator-scores.jsonl
scoring-manifest.json
analysis-report.html
```

没有相应真值时 evaluation 文件仍可生成，但状态必须为 `not_evaluated`，指标值为 `null`。

### 10.2 Provenance

`scoring-manifest.json` 至少记录：

- job ID、源视频路径/内容哈希、帧时间基准；
- Detect/Pose 权重路径和 SHA-256；
- 原始帧契约、主球员映射、事件规则、特征库、评分卡、feasibility 注册表版本；
- 人工标注集、评测协议、阈值/序数模型版本（没有时为 null）；
- 每个产物的路径和 SHA-256。

每个指标结果必须能沿以下路径回溯：

```text
indicator score/status
  -> feature result + feature function version
  -> event_id + event model/rule version
  -> primary_player_id + raw person_track_ids
  -> source frame indexes/timestamps
  -> source video + Detect/Pose model hashes
```

### 10.3 HTML 报告最小更新

- FS01/FS02/FS09 事件时间轴和边界不确定性；
- 当前 13 项指标的 F0～F4、status、阻断原因；
- 特征值、单位、valid/reason、raw/smoothed 摘要；
- Event 和 Feature 误差结果；
- 事件内 Track/QC 诊断；
- 证据帧索引、时间戳、原始 Track ID 和可选缩略图；
- 明显声明“候选事件/特征不等于正式评分”。

API 只需扩展产物白名单，不新增大规模页面或业务 API。

## 11. 预计文件修改范围

### 11.1 新增

| 路径 | 用途 |
|---|---|
| `contracts/metric-feasibility.schema.json` | F0～F4 注册表 |
| `contracts/primary-player-track.schema.json` | 稳定身份和原始 Track 映射 |
| `contracts/events.schema.json` | 预测事件 |
| `contracts/event-annotation.schema.json` | 人工事件真值 |
| `contracts/feature-observation.schema.json` | 统一特征结果 |
| `contracts/corrected-keypoints.schema.json` | 人工修正 Pose |
| `contracts/feature-evaluation.schema.json` | 特征误差 |
| `contracts/coach-annotations.schema.json` | 教练等级/排序 |
| `contracts/calibration-model.schema.json` | 阈值/序数制品 |
| `contracts/indicator-score.schema.json` | 单指标结果 |
| `contracts/scoring-manifest.schema.json` | 全链路版本和哈希 |
| `src/rallymate_vision/primary_player.py` | 稳定主球员路径 |
| `src/rallymate_vision/temporal_diagnostics.py` | 事件内时序诊断 |
| `src/rallymate_events/*` | 事件、标注、评测 |
| `src/rallymate_features/*` | 坐标、有效性、平滑、几何、运动学、事件特征、误差评测 |
| `src/rallymate_scoring/feasibility.py` | 成熟度注册表加载/验证/晋级门禁 |
| `src/rallymate_scoring/scoring.py` | 统一评分状态机 |
| `src/rallymate_scoring/calibration.py` | 教练标注和一致性报告 |
| `src/rallymate_scoring/backends/*` | 阈值规则与序数接口 |
| `metric-feasibility.json` | 初始六项历史注册表 |
| `metric-feasibility-pose-wave-v2.json` | 当前 13 项 F2 注册表；不包含 A～E 阈值 |
| 对应 `tests/test_*.py` 和小型 JSON fixtures | 单元、契约、评测、回归测试 |

### 11.2 修改

| 路径 | 修改目的 |
|---|---|
| `src/rallymate_vision/pipeline.py` | 在原始产物校验后调用独立 scoring-loop orchestrator；不重写 Detect/Pose 循环 |
| `src/rallymate_vision/validation.py` | 校验新产物、job/time/track/provenance 一致性 |
| `src/rallymate_scoring/granularity.py` | 保留原有 298 审计，给当前 13 项附加 feasibility 引用；不把 F 状态扩散成 298 项伪成熟度 |
| `src/rallymate_scoring/report.py` | 增加事件、13 项特征、误差和证据区块 |
| `src/rallymate_service/api.py` | 加入新产物下载白名单 |
| `src/rallymate_service/worker.py` | 仅在需要时传递 scoring-loop 配置/版本 |
| `contracts/summary.schema.json` | 新增可选 scoring-loop/manifest 摘要，保持现有 1.0.0 兼容读取 |
| `pyproject.toml` | 登记新增包和必要的 Schema 校验依赖；不引入大型时序框架 |
| `README.md` 和技术手册相关章节 | 在实现后记录新契约、运行方式和限制 |

## 12. 测试方案

### 12.1 单元测试

- 主球员路径：多人交叉、框面积变化、短遮挡、Track 碎片拼接、人工 seed 优先、歧义拒绝。
- 时序诊断：覆盖率、原始 ID 切换、左右交换候选、body-scale 跳点、最长缺失的精确合成样例。
- 事件：FS01/FS02/FS09 合成序列的边界和阶段；静止、噪声、缺失、事件重叠、片段截断不能产生伪高置信事件。
- 事件评测：完美匹配、错分类、漏检、重复事件、边界偏移和显式匹配协议。
- 几何：髋/肩中心、膝屈曲量、躯干角、支撑投影、角度周期展开。
- 运动学：非均匀 `timestamp_ms` 下速度/加速度的解析解；重复/倒序时间戳拒绝。
- 平滑/缺失：短缺失显式标记，长缺失无效，绝不把缺失变成 0，窗口不跨事件边界。
- 特征：十个初始基础特征及两组扩展代理（当前共 58 个定义）的 value/unit/valid/reason/evidence/raw/smoothed/版本契约。
- 误差评测：人为添加已知偏移，验证 MAE/P95/Bias、有效率、分视角和误差预算。
- 标定：无标定资产必为 `calibration_required`；非单调/无来源阈值拒绝；缺失序数制品拒绝；非法教练标签拒绝。
- 报告：必须从当前注册表派生并出现三类事件、13 个 ID、F 状态、特征、误差、阻断和证据，不出现伪 grade。

### 12.2 契约和集成测试

- 所有新 JSON/JSONL 示例通过对应 Draft 2020-12 Schema；故意缺字段、NaN/Infinity、0 代缺失、跨 job/track 引用必须失败。
- scoring loop 使用小型固定 `frames.jsonl` fixture 端到端生成全部派生产物。
- API/Worker 原有 round-trip 仍通过，新产物仅在存在时出现在下载列表。
- 真实短视频 smoke test 验证原 Detect + ROI Pose 不回归；真实事件准确率只在人工标注集上报告。

### 12.3 每个里程碑的固定验证命令

每个里程碑至少运行：

```powershell
.\scripts\run_tests.ps1
```

涉及 pipeline/API 的里程碑再运行短视频集成测试和现有产物校验。每次报告：修改文件、原有/新增测试数和结果、未解决问题、下一步。任何失败先在当前里程碑内收敛，不并行扩大修改面。

## 13. 里程碑与退出条件

### M0：审计与计划（本文件）

- 交付：现状、契约、特征定义、文件清单、测试、风险和阶段门禁。
- 已验证：28/28 原有测试；11,516 帧真实产物校验通过。
- 退出条件：用户确认计划。

### M1：Feasibility 注册表与评分安全状态机

- 初始新增六项 F0 注册表，随后由 v2 注册表扩展到当前 13 项；Schema、加载/验证和晋级证据检查共用。
- 统一 `scored / calibration_required / unavailable` 状态；不存在阈值时 grade 必须为 null。
- 测试重点：当前 13 项完整、版本字段、非法跳级拒绝、无真值不评分；派生计划不得硬编码旧数量。
- 退出条件：注册表机器可读且原 298 静态审计不回归。

### M2：稳定主球员与时序诊断

- 输出稳定身份映射和事件窗口可复用诊断。
- 不修改 Detect/Pose 原始契约，不依赖逐帧最大框。
- 测试重点：碎片拼接、歧义拒绝、事件内 ID 切换和关键点 QC。
- 退出条件：合成/fixture 上稳定身份正确；真实产物可生成诊断，但不把诊断当准确率。

### M3：FS01/FS02/FS09 Pose-only 事件层

- 输出 `events.jsonl`、人工事件导入和 Event F1/IoU/Boundary 评测。
- 无人工标签时评测状态为 `not_evaluated`，13 项成熟度不自动晋级。
- 退出条件：三类候选事件可评测、Schema 与回溯通过；获得并达到预注册真值目标后才可登记 F1。

### M4：版本化特征库与 13 项特征输出

- 实现十个初始基础特征、FS02 必需方向辅助特征，以及 FS09 和 FS01/FS02 的版本化 Pose 代理扩展。
- 输出单位、有效性、原因、证据帧、raw/smoothed 和版本。
- 退出条件：非均匀时间戳、缺失和平滑测试通过；13 项均能输出合法 feature result（值或明确 unavailable/calibration_required）。

### M5：特征真值误差与误差预算

- 导入人工校正点/事件边界；输出 MAE/P95/Bias/有效率/分视角和分层误差预算。
- 退出条件：已知误差 fixture 精确通过；真实标注集有结果后才评估 F2 晋级，未有真值不晋级。

### M6：教练标定接口和两后端契约

- 导入 A～E/排序，生成一致性报告；实现阈值规则和序数模型的安全加载/拒绝接口。
- 当前无真值时只交付 Schema、校验、报告和 `calibration_required`。
- 退出条件：不存在默认/经验阈值；有合法外部标定资产时统一契约可输出 grade，否则安全拒绝。

### M7：报告、产物发布和端到端验收

- HTML 展示事件时间轴、当前注册表 13 项 F 状态、特征、误差、阻断和证据。
- 生成 scoring manifest 和产物哈希，API 白名单兼容扩展。
- 退出条件：本轮七条最终验收逐项有证据；F3/F4 仍取决于后续真实标定和独立测试，不因工程完成而伪晋级。

### M13：Pose Wave v2 的 13 项收口

- 将 FS09-M01/M02 与本里程碑新增的 FS01-M03/M04、FS02-M03/M04/M05 纳入同一 v2 注册表，当前合计 13 项 F2。
- 在 Halpe26 与 WholeBody133 的同一 600 帧、31–51 秒窗口生成事件、特征、指标和安全评分状态；具体计数以 `reports/fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json` 为准。
- 五个新指标仅声明足部/下肢运动学代理，不声称真实离地、落地、接触或支撑力；`grade` 和 `threshold_version` 必须继续为空。
- 退出条件：注册表、派生 measurement plan、同窗产物和报告指标集合一致；F3/F4 仍因人工真值、教练标定和独立测试阻断。

### M14：模型替换接入与公平 A/B 诊断

- 将 `rtmpose-m-wholebody133-analysis` 作为显式候选接入服务配置、常驻 Worker、请求契约和部署预设；原生拓扑必须保持 `coco_wholebody133`，YOLO 仍作为未晋级前的默认回滚档。
- 在同一 600 帧窗口输出三路动态标注视频，并分别比较候选事件切分与固定公共事件边界下的特征差异；比较结果必须声明 `ground_truth_provided=false`、`accuracy_claim=false`。
- 以当前 v2 注册表动态驱动 Worker 烟测，验证 13 项的事件、特征、质量门禁、`calibration_required/unavailable` 和产物哈希，不允许烟测脚本保留六项硬编码。
- 退出条件：WholeBody133 能经真实 Worker 跑通且 bundle 校验通过；跨模型差异和不可比较项有机器可读报告；未获得人工真值前不切换默认模型、不晋级 F3/F4。

### M15：标定信任边界与事件级等级报告收口

- 独立测试必须从封存样本的原始多教练标签重算一致结论，拒绝伪造 `resolved_grade`、重复教练、缺失阶段/语义或未绑定的外部裁决；candidate provenance 按 artifact scope 强制绑定数据集和协议 ID、版本及哈希。
- 生产标定资产不能凭自身声明的 `passed/F4/approved` 直接评分；必须由运维受控的受信晋级账本交叉校验完整 promotion lineage、资产哈希、独立测试报告、人工决定和 F4 注册表后，才生成不可序列化的运行时授权包装器。
- scoring-loop v0.4.0 动态传播每指标/每事件 `scored / calibration_required / unavailable` 与事件级 A～E 分布，并用注册表的 `required_events` 对必需关键阶段做指标级测量门禁；即使特征缺失或 hard fail 先触发，也保留全部并发 score-only 阻断原因。合法 F4 报告显示事件 ID、等级、置信度、标定版本、原因和反馈，但 `result_state.grade` 保持 null，明确不设计跨事件或跨指标总分。
- production promotion 必须验证完整的 F0→F4 maturity-evidence，且运行时必须把受信资产绑定到晋级时同一 registry version、canonical hash、indicator、required events 和 required features；不得只相信资产或注册表自声明的 `F4/passed`。
- 当前真实产物仍为 F2：Halpe26 同窗特征可测 130/130、评分状态 69 `calibration_required` / 61 `unavailable`；WholeBody133 同窗特征可测 143/143、评分状态 44 / 99；Halpe26 全片在 M27 分离边界证据与特征测量后为 400/442 特征可测、评分状态仍为 176 / 266。所有代理阶段和质量告警继续阻断正式等级；三者均 0 grade、0 threshold。评分门禁数不能冒充特征可测率或模型准确率。
- 退出条件：手写或篡改的生产资产 fail closed；合法 promotion→ledger→pipeline→单指标评分合成回归通过；当前无真值路径不回归；本轮最终全仓测试与动态报告媒体/链接复检全部通过。

### M16：候选事件、身份、排序标定与产物完整性加固

- 事件候选器升级为 `pose-motion-bout-v0.3.0`：完全静止 Pose 和只有全骨架共模抖动、缺乏持续位移与方向连贯性的序列不再回退生成事件；检测参数只用于候选分割，仍不是 A～E 阈值。
- 主球员升级为 `primary-player-v0.2.0`：候选排序与同分决策确定化，输出全段 Viterbi max-marginal、次优分数、margin、竞争 Track 与歧义状态。实际 timeline 版本必须逐行一致并匹配 registry，否则在创建输出目录前 fail closed；旧 timeline 不得由当前代码常量伪装成 v0.2。
- 每个指标从 registry 动态解析必需阶段；阶段键缺失或值不可观测会进入明确质量门禁。关键点跳变、左右交换候选、身份歧义/Track switch 和 FS02 目标方向缺失可保留 F2 特征审计，但禁止正式 A～E。
- `validate_run.py` 对权威 `features.jsonl`、压缩指标特征、score evidence 和 Summary 做内容级相等与 SHA-256 校验；即使攻击者同步改写多层数值，只要未更新受信 Summary 哈希也会拒绝。
- 新增 ranking-only 标定链路：保留逐教练原始排序、成对偏好、group holdout 与独立测试 seal，拟合 Bradley–Terry 相对排序。输出始终为 `relative_order_only` 且所有 grade/threshold 为空；不能被 A～E loader 或生产 promotion 接收。
- 退出条件：静止/抖动负例、真实动作正例、timeline 版本错配、阶段闭合、跨产物篡改和 ranking fail-closed 测试通过；最终 v0.5 产物实际绑定 v0.2 timeline、完整哈希与 canonical video identity。

### M18：指标相关关节质量证据

- 主球员诊断升级到 `primary-player-v0.3.0`，在事件内同时输出聚合 jump/swap 帧和 `keypoint_jump_candidate_frames_by_joint`、`left_right_swap_candidate_joint_pairs`；这些仍是启发式候选，不是真值或准确率。
- quality policy `v1.3.0` 从 registry 的 required features 解析 `FEATURE_DEFINITIONS.required_joints`。只有候选异常关节与该指标依赖集合相交时保留正式评分阻断；无交集时改为 `*_outside_indicator_joints` 审计告警。
- 旧事件、空列表、未知 feature 或畸形 joint provenance 不享受降级，继续使用 broad flag fail-closed；相关髋/膝/踝/肩异常、身份连续性、必需 phase、覆盖率和战术目标方向门禁均未放宽。
- v0.3 与 v0.2 的全片 2,911 行和同窗 600 行主球员选择结果除版本字段外逐行完全相同；本里程碑改善来自更精确的依赖归因，不是更换 Track、重推理或调整 A～E 阈值。

### M19：并发评分阻断完整归因

- 旧评分分支在 required feature 已失效或事件 hard fail 时会提前返回，`quality_gate.scoring_block_flags` 虽仍存在，但身份连续性、关键点跳变、左右交换或战术目标方向等并发原因不会进入 `reason_codes` 汇总。
- v0.4 将每个活动 score-only flag 映射为独立语义码与中文反馈，并在所有提前返回路径合并去重；不再把目标方向或 Pose 诊断误写成身份问题，也不因首个失败隐藏其他修复项。
- 运行产物校验对 v0.4+ 要求 `event_scoring_quality_blocked` 和全部原始 `scoring_block_flags` 都出现在指标/评分记录的 `reason_codes`；同时修改两份派生 JSONL 仍无法绕过 Summary 的 SHA-256 绑定。
- 该变化只增强解释和审计，事件数、特征值、`feature_status`、`scoring_status`、grade 与 threshold 均未改变；当前仍为 13 项 F2、0 grade、0 threshold。
- 真实产物复核的 flag→reason 对应数完全一致：Halpe 同窗 jump/swap/target 为 34/33/10，WholeBody 同窗为 88/27/11，全片 Halpe identity/jump/swap/target 为 39/193/69/32；同窗 identity reason 均为 0。计数有事件/指标重叠，只是阻断归因，不是准确率。

### M20：Pose 诊断视频复核队列

- 新增 `pose-diagnostic-review-queue-v1.0.0`，将同一源帧/关节在 FS01/FS02/FS09 和多个指标记录中的重复诊断折叠为一个人工任务，同时保留所有事件与指标实例反向引用。
- 每个任务绑定源 frames、primary timeline、events、scores、summary 的 SHA-256，并记录候选帧前/中/后三帧模型坐标、置信度、source Track、视频 seek 时间和显示视频 SHA；模型坐标明确不是人工校正点。
- 三套当前队列为：Halpe 同窗 39 任务/35 唯一帧/53 受影响指标实例，WholeBody 同窗 80/59/91，Halpe 全片 261/198/238；全片任务进一步分为 jump 202、swap 43、source Track switch 16。
- 复核页使用连续 H.264 视频，浏览器只保存 localStorage 草稿并导出 CSV。非 pending 决定必须带 annotator 与时间；Python 校验后仍固定为 `not_adjudicated`，不会自动解除门禁、晋级成熟度或生成等级/阈值。

### M21：边界受限阶段代理

- `pose-event-phase-proxies-v0.2.0` 在活动 run 正好抵达 FS01 右边界时保留已观测的下行活动峰，并标记 `phase_proxy_right_censored_peak:landing_proxy_ms`；它不是减速、触地或落地真值。
- FS02 峰后只有两帧有效减速样本时，不再因为无法拟合三样本自适应包络而丢弃全部证据；仅保留实测减速度峰，并标记 `phase_proxy_low_sample_peak:first_step_slowdown_proxy_ms`。只有一帧时仍保持 null/unavailable。
- 左右踝相对速度峰差小于 5% 时保留确定性的较大峰侧作为 `lead_foot_side_proxy_ambiguous`，但不把它当成真实启动脚。quality v1.4 只阻断依赖 landing/first-step/lead-side 的指标评分，不误伤同事件其他指标。
- 真实无 GPU 重放使两个 600 帧同窗分别达到 130/130 与 143/143 特征 measured；全片由 309/416 提升为 319/416。评分状态完全不变，说明本里程碑没有借代理阶段绕过 A～E 门禁。

### M23：Pose 诊断全时间线真值评测

- 候选队列只能复核模型报告的正候选，缺少显式负样本域时不能计算 recall。本里程碑新增 coverage CSV：按诊断类型声明已完整审阅的源帧区间；只有 accepted `all_model_relevant_scopes` 区间内，缺少 truth positive 才表示人工确认的负样本。
- positives CSV 稀疏记录人工确认的 keypoint jump joint、左右 swap joint pair、primary identity ambiguity 或 source Track 身份连续性失败；v1 固定 exact source frame 匹配，不在看到结果后选择容忍窗口。
- 每条 accepted 人工输入要求至少两名唯一标注者、一名独立裁决者和带时区时间；跨视频、越界帧、未知关节、重复 identity、重叠 accepted coverage、弱复核或任何 queue/run/video SHA 不一致都会拒绝。
- Halpe26 与 WholeBody133 同窗各生成 600 帧连续视频盲审工作台，并接入主模型对比报告。当前 4 类诊断 coverage/positive 均为 0，评测状态 `annotation_required`，precision/recall/F1 为 null；工具不会自动修改 quality policy、成熟度、grade 或 threshold。
- 新增/更新测试覆盖空白包、完整/部分覆盖、TP/FP/FN、弱复核、未知来源、内容哈希、不可覆盖人工文件和报告安全语义；仓库全量回归为 370 tests OK（2 skipped）。

### M24：Pose 诊断接受协议与策略审核

- 新增 `pose-diagnostic-gate-acceptance-protocol-v1.0.0`，但不提供任何默认样本量、precision、recall 或 F1 数值。可执行协议必须由外部可信登记在结果揭示前冻结，并绑定 quality/evaluation/matching 版本、四类诊断和每个预期范围的 manifest/queue/artifact SHA。
- `pose-diagnostic-quality-gate-review-v1.0.0` 不信任 evaluation 自报比例：先重新读取绑定的 coverage/positives CSV，重新运行全时间线评测，要求完整报告精确一致，再以 TP/FP/FN 原始计数跨范围汇总。
- 协议晚于评测、范围缺失/多出、CSV 被修改、比例或计数伪报都会拒绝。全部条件满足也只能输出 `eligible_for_human_policy_review`，绝不自动修改 quality policy、生成 A～E、晋级成熟度或创建阈值。
- 当前两个真实同窗范围没有人工覆盖，也没有外部协议，`reports/pose-diagnostic-quality-gate-review-v1-empty.json` 状态为 `annotation_and_protocol_required`，两项阻断分别为协议缺失和全时间线真值缺失。
- 定向测试覆盖协议前置时序、范围绑定、失败指标、CSV 后改重算、CLI 不覆盖和安全语义；当前全仓回归为 395 tests OK（2 skipped）。

### M25：事件局部 Pose 覆盖率与产物重绑定

- `pose-motion-bout-v0.3.1` 不再把一个父 motion bout 的平均 Pose 运动学覆盖率复制给 FS01、FS02、FS09 三个子事件。每个事件在自身闭区间内独立记录 `sample_count`、`valid_sample_count`、`coverage_fraction`、门禁状态和语义；`pose_kinematic_coverage_low` 必须与该状态精确一致。
- Python validator 与 `events.schema.json` 对 v0.3.1 coverage provenance 实施 fail-closed 校验，拒绝缺字段、计数/比例矛盾、状态或 quality flag 篡改。0.75 沿用为事件检测信号质量门槛，明确不是 A～E 标准。
- 真实全片审计发现旧父区间口径有 15 个事件与局部口径不一致。无 GPU 重放后，416 条指标实例的特征可测数由 319 提升到 368（+49），评分层 `calibration_required` 由 149 提升到 167（+18）；两个 600 帧同窗因原本局部覆盖均完整，计数保持 130/130 和 143/143。
- registry、事件/特征/评分 bundles、固定边界 A/B、跨模型事件分歧、诊断队列/真值包、真值 source binding、不可变空标定数据集和动态报告全部重新绑定到 `.9 / event v0.3.1`。人工真值仍为 0，所有 grade/threshold 仍为空，F2 未晋级。
- 新增 coverage 作用域/篡改回归后，全仓 `scripts/run_tests.ps1` 为 390 tests OK（2 skipped）；三个真实运行 bundle 均通过跨产物校验，13 个 prepared 文件都通过 non-ready 校验且全部被 fit-ready 门禁拒绝。

### M26：事件运动参考与 FS02 后段相位锚点

- `fs01-fs02-pose-proxies-v0.4.0` 让 FS02-M05 的后段方向一致性、相位时差和步后站距直接使用事件层版本化 `first_step_slowdown_proxy_ms`，不再在特征层重新选择另一个全局晚峰；声明阶段无法在 160 ms 时间合同内对齐时 fail closed，不回退到另一个信号峰。
- `pose-motion-bout-v0.4.0` 继续优先使用双肩/双髋 body center；肩部缺失但双髋可见时，仅为候选事件运动信号使用由直接重叠帧 robust median 偏移对齐的 hip center。全程无插值、无关键点回填，逐事件记录 body/hip fallback/missing 样本数、偏移与语义；特征函数仍按各自 required joints 独立门禁。
- 静止和 hip-only 静止负例继续输出 0 事件；肩部缺失的合成真实运动保留候选；body/hip 来源切换的恒定几何测试证明不会制造坐标跳变。Python validator 与 `events.schema.json` 要求 v0.4.0 的 `event_motion_reference` provenance，计数或语义篡改会拒绝。
- 与 v0.3.1 旧全片候选一一匹配的记录中，11 条指标实例由 `unavailable` 恢复为 `measured`，没有 matched 实例退化；新运动参考还暴露两组额外候选 bout。最终全片为 FS01/FS02/FS09 各 34 段、442 条指标实例、392 条特征 `measured`，评分层 176 `calibration_required` / 266 `unavailable`。新增候选是否真实仍必须由 Event F1/Boundary MAE 真值评测决定。
- M26 当时以 registry `.11` 绑定三套 bundles、固定边界 A/B、事件分歧、诊断队列/真值包和空标定数据集；该条是历史里程碑，随后由 M27 的 `.12 / quality v1.5.0` 取代；当前权威绑定见 M29 的 `.14 / quality v1.6.0`。

### M27：测量门禁与正式评分门禁解耦

- `indicator-event-quality-v1.5.0` 不再把 `pose_kinematic_coverage_low` 当作 required feature 的测量失败。它只表示自动候选事件边界的运动学证据不足：完整特征保留为 `feature_status=measured`，同时进入 `scoring_block_flags`，score 以 `event_boundary_evidence_low` 明确返回 `unavailable`。
- `primary_pose_coverage_low`、`primary_track_coverage_low`、confirmed ID switch、必需 phase 缺失和适用的 restabilization 缺失继续硬阻断测量；实际 required feature 无效也继续 unavailable。没有把 0 当缺失，也没有降低任何关键点/阶段门槛。
- 真实全片重放从 392/442 提升到 400/442 feature measured；恢复的 8 条记录都已经具备完整 required feature。评分状态精确保持 176 `calibration_required` / 266 `unavailable`，44 条受边界覆盖诊断影响的 score 带 `event_boundary_evidence_low`，grade=0、threshold=0。
- registry `.12`、三套运行 bundle、同窗/固定边界对照、诊断队列与空白真值评测、真值 source binding、空标定数据集 `rallymate-calibration-6f6506b09724c0fd`、三套 Worker 烟测和动态报告已重新绑定。全仓 396 tests OK（2 skipped）；人工事件、人工关键点、人工语义和教练标签仍全部为 0，13 项保持 F2。

### M28：右边界减速阶段的可证实恢复

- 审计 42 条 feature `unavailable` 后确认：39 条含无效 required feature，其中两条 FS01-M03 双脚上抬持续时间因双脚未被完整观察，必须继续为 `null`；另有两条虽特征完整但主球员 Pose 覆盖 hard fail；仅一条 FS02-M05 是事件右边界速度峰后已有清晰减速证据但旧规则无法确认。
- `pose-motion-bout-v0.4.1` / `pose-event-phase-proxies-v0.3.0` 只在峰值恰位于事件右边界、其后两个已有观测样本时间间隔均不超过 160 ms、且速度严格逐样本下降时，把该边界峰保留为 `first_step_slowdown_proxy_ms`。事件外样本只用于确认右截断趋势，不进入事件特征、不延长事件区间，也不声称接触或落地。
- plateau、NaN、过大时间间隔和不足两个后续样本都继续返回 `null`；恢复记录带 `phase_proxy_right_censored_peak:first_step_slowdown_proxy_ms`，quality policy 仍阻断其 A～E。
- 真实全片候选事件数与边界完全不变，只有 FS02-031 的该阶段由 `null` 变为 88,600 ms；feature measured 从 400/442 提升到 401/442，score 状态精确保持 176 `calibration_required` / 266 `unavailable`。两个同窗仍为 130/130 与 143/143 feature measured。
- registry `.13`、三套运行 bundle、同窗/固定边界对照、事件分歧、诊断队列/空白真值评测、truth source binding、不可变空标定数据集 `rallymate-calibration-1595a0ef86737cc5`、三套 Worker 烟测和动态报告已重建；全仓 397 tests OK（2 skipped）。人工真值仍为 0，13 项保持 F2。

### M29：主球员 Pose 覆盖的测量/评分双门禁

- 对 M28 剩余 41 条 feature `unavailable` 逐条复核后，39 条确有 required feature 或必需阶段无效；另外两条 FS01-M02/FS01-M05 的 required features 全部有效，只是所属事件 `fs01-008-f45ea4c72b18` 的事件级主球员 Pose 覆盖率为 0.419355。已观测 Pose 的关键点有效比例为 0.980769，Track 覆盖率为 0.806452。
- `indicator-event-quality-v1.6.0` 因此把 `primary_pose_coverage_low` 从 measurement hard fail 改为 score-only block：`measurement_allowed=true`，但 `scoring_allowed=false`，并输出 `primary_pose_observation_coverage_low`。这不插值、不补点、不改变特征数值，也不降低 required feature 自身的有效性门槛。
- `primary_track_coverage_low`、confirmed ID switch、必需 phase 缺失、适用的 restabilization 缺失和 required feature 无效继续 measurement hard fail。正式 A～E 的身份、事件、关键点、语义、标定与独立测试门禁没有放宽。
- 真实全片无 GPU 重放中只有上述两条从 `feature_status=unavailable` 恢复为 `measured`，特征层由 401/442 变为 403/442；评分层精确保持 176 `calibration_required` / 266 `unavailable`，grade=0、threshold=0。两个同窗继续为 130/130 与 143/143 feature measured，评分状态继续为 69/61 与 44/99。
- registry `.14`、三套运行 bundle、同窗/固定边界对照、事件分歧、诊断队列/空白真值评测、truth source binding、新不可变空标定数据集 `rallymate-calibration-21535d0a03c52856`、三套 Worker 烟测和动态报告均已重建。全仓 399 tests OK（2 skipped）；人工事件、关键点、语义和教练标签仍为 0，13 项保持 F2。

### M30：全片 Pose 配置路由审计

- 对同一 2,911 帧、97 秒视频真实运行 Halpe26 384×288 与 WholeBody133 256×192，并保留现有 Halpe26 256×192 作为基准。三者 Pose 出帧集合完全相同：2,673 帧有主球员 Pose，差异来自关键点置信度和坐标，而不是新增人物检测帧。
- 为消除自动事件边界混杂，使用 Halpe256 的同一 102 个事件 ID、Track、起止时间和关键阶段重算全部 13 项 required features。固定边界结果为 Halpe256 `403/442`、Halpe384 `399/442`、WholeBody133 `369/442` 条指标事件完整；WholeBody 没有新增完整的 model-only 指标事件，384 只有 2 条 model-only、同时丢失 6 条基准可测实例。
- Halpe384 在各自切分边界上为 `415/442`，但与 Halpe256 的自动候选在 IoU≥0.3 时只匹配 `90/102`，平均 Segment IoU `0.90767929`，中心边界平均绝对差 `64.2556 ms`。WholeBody 为 `90/102` 对 `99` 个候选，中心差 `95.05 ms`。因此各自切分覆盖不能用于模型净提升或准确率结论。
- 新增 `pose-profile-routing-audit-v1.0.0`、JSON Schema 和回归测试。路由决定继续保留 Halpe256 为当前评分主配置；Halpe384 保留为分析候选，WholeBody133 保留为精细可视化与未来手/脸/足拓扑证据。禁止逐事件或逐特征跨模型 cherry-picking，也不允许无审计运行时 fallback。
- 完整机器证据位于 `reports/pose-profile-routing-audit-full.json`、两份全片 fixed-boundary 报告和两份全片 event-disagreement 报告。该里程碑不修改 registry、特征函数、质量门禁、grade 或 threshold；13 项仍为 F2，模型准确率仍需人工事件和关键点真值。
- 新增与报告相关定向测试 15/15 通过；全仓 `scripts/run_tests.ps1` 为 402 tests OK（2 skipped）。路由报告通过 Draft 2020-12 Schema 校验，动态 HTML 已重新生成。

### M31：评分阻断互斥归因与人工真值优先级

- 新增 `scoring-blocker-audit-v1.0.0`，逐条读取 score 内嵌 required features、quality gate、原始 scoring block flags 和 typed reason codes，并与 scoring-loop Summary 计数交叉校验。目标方向、跳点、左右交换、身份连续性、边界覆盖和阶段代理均必须映射到各自 reason，不能用泛化身份原因替代。
- 全片 442 条记录互斥拆分为：176 条 `calibration_required`；227 条 required features 完整但存在 score-only 证据阻断；3 条仅 feature 不完整；33 条 feature 不完整且同时存在 score-only 阻断；3 条事件 hard fail。后四类合计精确等于 266 条 `unavailable`。
- “可恢复”严格表示：若对应人工真值、外部接受协议和受控 quality policy 发布全部完成，完整特征记录最多从 `unavailable` 变为 `calibration_required`，不会直接成为 `scored`。当前没有自动改变任何门禁。
- 按“该 flag 是唯一阻断”的指标实例数排序，关键点跳变为 100 条、左右交换为 19 条、目标方向语义为 14 条、事件运动学覆盖为 3 条、两个右边界阶段代理各 1 条；多阻断实例必须完成所有相关真值，不能重复相加。
- 机器报告 `reports/scoring-blocker-audit-halpe256-full.json` 绑定 scores/summary SHA-256、registry `.14`、quality v1.6、loop v0.4.1，并已接入动态报告。该报告只用于人工标注优先级，不是 precision/recall、评分准确率或阈值。
- blocker 审计与动态报告定向测试 16/16 通过；全仓 `scripts/run_tests.ps1` 为 405 tests OK（2 skipped），审计 JSON 通过 Draft 2020-12 Schema 校验。

### M32：视频可定位的人工真值优先工作清单

- 新增 `scoring-truth-priority-worklist-v1.0.0`，只纳入 227 条 required features 完整、无 hard fail、但被 score-only evidence gate 阻断的指标实例；它不把 feature 不完整记录误放进“复核后可恢复”清单。
- 将每个实例按 `event_id × diagnostic_flag` 分组为 129 个工作项，记录事件码、起止时间、主 Track、受影响指标实例、单一阻断实例、并发 flags、所需人工真值和 SHA-256 来源绑定。排序先按“单一阻断实例数”，再按全部影响数和时间，不是准确率排名。
- jump、swap、source Track/identity 类项目与现有 Pose 诊断队列逐任务关联：80 个 Pose 工作项全部找到 task ID 和视频候选时刻，缺失关联为 0。目标方向、事件边界、启动脚侧别和阶段代理明确路由到评分事件/语义真值，而不是伪造 Pose task。
- 输出 `reports/scoring-truth-priority-worklist-halpe256-full/index.html`、`worklist.json` 和 UTF-8 CSV。HTML 内嵌现有 97 秒 H.264 动态视频，点击时间按钮可直接 seek；同时链接 Pose 诊断与评分真值工作台。
- 生成物固定为 `annotation_required`、accepted annotations=0；不把模型候选当真值、不修改 quality policy、不生成 grade/threshold、不晋级 maturity。即使未来全部工作项通过，在没有教练标定时最高状态仍为 `calibration_required`。
- M32 完整回归为 408 tests OK（2 skipped）；主报告与工作清单共 66 个本地引用均存在，HTML/CSV 的本地 HTTP 入口均返回 200。

### M33：特征不可测实例的逐事件观测缺口审计

- 新增 `feature-observation-gap-audit-v1.0.0`，将全片 442 条指标实例中的 `feature_status` 与评分状态分开处理，并逐条校验 registry required feature 顺序、indicator compact feature 与源 `features.jsonl` 的 value/unit/version/valid/reason/source frames 一致性。
- 当前特征层为 403 `measured` / 39 `unavailable`。39 条集中在 11 个候选事件，共出现 178 次 required-feature 失败；按 `event_id × feature_name` 去重后为 138 个真正观测缺口。
- 138 个唯一缺口中，134 个是 `valid_fraction_below_quality_gate`，影响 37 条指标实例、9 个事件；2 个是双脚上抬代理不可观察；另有 1 个姿态波动样本不足和 1 个速度/角速度样本不足。缺失继续保留 null/invalid，不使用 0、插值或降低有效率门槛。
- 审计绑定 Halpe256/384 与 Halpe256/WholeBody133 两份全片 fixed-boundary 报告。对当前 39 条 unavailable，384 只让 2 条在相同候选边界下变为 measured，同时使 6 条当前 measured 变 unavailable；WholeBody 恢复 0 条、另丢失 34 条。因此替代模型结果只作为可观测性旁证，不是准确率，也不能启用逐事件/逐特征 fallback。
- 机器报告为 `reports/feature-observation-gap-audit-halpe256-full.json`，包含 11 个事件的 seek 时间、全部无效特征、有效率/所需关节/raw 样本计数、受影响指标和安全恢复动作，并已接入动态 HTML。报告不修改 measurement/scoring gate，不生成 grade/threshold，不晋级 maturity。
- M33 Schema 与实例通过 Draft 2020-12 校验；全仓 `scripts/run_tests.ps1` 为 411 tests OK（2 skipped）。

### M34：小 ROI Pose 可观测性恢复实验

- `PoseEstimator` 新增显式 `min_roi_size_px` 参数，但默认值仍为 32；服务、Pipeline 和既有运行配置没有切换。实验值 8 只能由独立实验脚本传入。
- 对 M33 的 11 个缺口事件逐帧追溯后，312 个相关源帧中有 171 帧已有主球员 Pose；131 帧属于当前 `max_players=2` 调度内、仅因 32px 最小裁剪保护而跳过；另有 9 帧主检测缺失、1 帧不在原调度范围。
- `small-roi-pose-recovery-v1.0.0` 保持视频、检测、主球员 timeline、Halpe26 256×192 权重、102 个候选事件和 442 条指标实例完全相同，仅对上述 131 帧用 8px 最小 ROI 重跑 GPU Pose。131/131 帧得到 Pose；这是模型输出可观测性，不是人工关键点准确率。
- 固定边界重算结果为：403 条原 measured 继续 measured，20 条 unavailable→measured，19 条仍 unavailable，0 条 measured→unavailable。因此实验特征完整率可从 403/442 提升到 423/442，但 scoring gate、A～E、maturity 和生产路由均未改变。
- 动态视频 `reports/experiments/small-roi-pose-recovery-halpe256-full-v1/small-roi-pose-recovery-comparison-browser.mp4` 为 H.264、1920×720、390 帧、13 秒，左右逐帧展示当前 32px 与实验 8px、全画面和主球员放大、有效点数与候选事件源时间；首/中/末帧均解码通过。
- 机器产物为同目录 `report.json`、`fixed-boundary-comparison.json`、`frames.experimental.jsonl` 与 `video-validation.json`。全部声明 `accuracy_claim=false`、`production_enabled=false`、无 grade/threshold/F3/F4。生产启用前至少需要按远场框尺寸、机位和遮挡分组的人工关键点 MAE/P95/Bias 与独立测试。
- M34 Schema 实例为 0 errors；视频首/中/末帧均解码并完成目视抽查；全仓 `scripts/run_tests.ps1` 为 417 tests OK（2 skipped）。主报告 69 个本地引用 0 缺失，主页面、MP4 和实验 JSON 的本地 HTTP 入口均返回 200。

### M35：小 ROI 关键点独立真值与误差评测

- `small-roi-keypoint-truth-pack-v1.0.0` 精确绑定 M34 experiment report、实验 frames、主球员 timeline、原视频、M33 gap audit 和动态比较视频的 SHA-256；任务集合另用 canonical SHA-256 冻结，修改 task、视频或实验帧都会拒绝编译。
- 全部 131 个恢复帧进入任务集，每帧包含左右肩、髋、膝、踝、大脚趾、小脚趾和脚跟，共 1,834 个关节点任务。分层仅使用 bbox 长边的经验四分位作描述，不把它作为通过门槛。
- 工作台 `data/annotations/small-roi-keypoint-truth-halpe256-v1/review.html` 只播放无模型骨架的原视频并对检测框做放大；bootstrap 中没有模型 `x_px/y_px` 或 keypoints。两名标注者分别导出 raw CSV，第三名 reviewer 只能引用两份不同标注者的结果后逐点裁决。
- `small-roi-keypoint-error-v1.0.0` 在完整裁决后计算像素 Euclidean MAE/P95、bbox 长边归一化 MAE/P95、x/y Bias、预测有效率，并按 joint 和 bbox 四分位拆分。PCK 的 value/threshold 固定为 null，直到外部协议预注册阈值。
- 当前 raw annotations=0、accepted adjudications=0、状态为 `annotation_required`；`reports/small-roi-keypoint-error-halpe256-v1-empty.json` 的 metrics/per-joint/per-stratum 都为 null，routing switch=false，生产默认仍为 32px。
- 三份新 Schema 的定义和真实实例均通过 Draft 2020-12 校验；新增空包、完整双人裁决、reviewer 身份冲突、任务篡改和“CSV 修改后未重编译”拒绝测试。全仓 `scripts/run_tests.ps1` 为 423 tests OK（2 skipped）。

### M36：13 项可计算性证书与残余不可用动态审计

- 新增 `residual-indicator-computability-v1.0.0`。审计按当前 registry 精确检查 13 项 required feature，并要求每个 measured 实例的值非 null、`valid=true`、单位/特征版本与函数注册一致、置信度存在且 `source_frames` 非空；不能仅凭“记录存在”宣称可计算。
- 实验 8px 固定边界共 442 条指标实例，423 条 measured、19 条 unavailable。13/13 个指标都有真实 measured 实例，每项为 30～34 条，`indicator_without_measured_instance_count=0`。这满足“当前 13 项均能在合格输入上有效计算”的工程证据，但不把候选事件当真值。
- 19 条 residual 集中在 6 个候选事件并被互斥分类：12 条 `primary_bbox_clipped_at_image_boundary`、6 条 `primary_source_track_transition`、1 条 `video_start_boundary_censored`。前者是身体关节已在源画面外，后两者分别缺身份连续性真值或事件前置上下文。
- 动态证据 `reports/pose-scoring-ab/residual-computability-evidence-browser.mp4` 为 H.264、1280×720、143 帧、4.766667 秒；首/中/末帧均可解码并已目视确认标题、Track、有效点数、选中框和实际画面原因一致。机器证书为 `reports/residual-indicator-computability-small-roi-v1.json`，视频验证为 `reports/residual-computability-video-validation.json`。
- 审计和渲染契约强制 `zero_fill_used=false`、`feature_quality_gate_modified=false`、`cross_model_cherry_picking_used=false`、`production_route_changed=false`、grade/threshold/maturity 全为 false。对这 19 条继续返回 `unavailable` 是正确行为，不属于应通过放宽门槛追求的失败率。
- M36 不能替代 M35 的人工关键点误差，更不能将 F2 推进到 F3/F4。生产仍使用 32px 最小 ROI；实验 8px 只有人工真值、分视角误差预算和独立测试通过后才可讨论路由变更。
- M36 机器 Schema 定义与真实实例通过 Draft 2020-12；主报告 49 个去重本地引用 0 缺失，页面、视频、审计 JSON 和盲标工作台均由本地 HTTP 返回 200；全仓 `scripts/run_tests.ps1` 为 428 tests OK（2 skipped）。

### M37：正常上传默认 Pose 后端替换为 RTMPose

- `models/rtmpose/deployment-presets.json` 的默认 preset 已从 YOLO 改为 `rtmpose-m-halpe26-online`；Service、常驻 Worker、本地推理脚本、Demo 脚本、Docker Compose 和通用请求示例均通过同一 preset 注册表解析。YOLO person detection 不变，`yolo-baseline` 保留为显式回滚 Pose preset。
- 没有设置 `RALLYMATE_POSE_PRESET`、也没有传 `--preset` 覆盖的真实 GPU Worker 已走通 `JobDatabase -> PersistentVisionRunner -> process_one -> run_pipeline`。120 帧耗时 3.893 秒、30.822 FPS，实际后端为 RTMPose-M Halpe26 256×192，bundle passed；当前 13 项生成 26 条记录，7 条 `calibration_required`、19 条 `unavailable`、grade=0、threshold=0。
- 新增 `default-pose-routing-audit-v1.0.0`，强制核对 deployment default、烟测 `preset_source=deployment_registry_default`、实际 backend/topology、当前 13 项 registry 精确集合、模型 SHA-256、bundle 状态和无等级/阈值门禁。默认烟测与 97 秒全片证据使用同一 RTMPose checkpoint SHA。
- 保持生产 32px 最小 ROI 时，全片同一 102 个候选事件上为 403/442 条特征实例 measured、39 条 unavailable；13/13 指标都至少有一个真实 measured 实例，每项 28～33 条。实验 8px 的 423/442 结果没有进入默认服务，生产 ROI 保护未改变。
- 该替换只把正常上传的 Pose 观测入口升级到 Halpe26，并证明当前 F2 特征合同可执行；它不证明事件/关键点/等级准确率，不推进 F3/F4，不授权 A～E。机器证据为 `reports/default-pose-routing-audit-m37.json` 与 `runs/rtmpose-m-halpe26-default-m37-smoke/`。

### M38：每次上传输出 13 项计算就绪状态

- Pipeline 在事件、特征与 score 产物完成后自动生成 `calculation-readiness.json`。它以当前 feasibility registry 为成员真源，逐指标列出 required event、required feature、候选实例数、`feature_status`、评分状态、证据帧、特征失败原因、measurement hard flag、score-only flag 和不放宽门禁的修复动作。
- “有效计算”严格定义为：该视频至少有一个真实候选事件，其全部 required feature 均为 `valid=true`，并具有非空 source frames。它不等于 Event F1、人工关键点/特征误差、等级区分度或 F4；报告强制 `formal_scoring_ready=false`，不生成 grade/threshold，也不把缺失写成 0。
- 无环境或 CLI preset 覆盖的真实 GPU Worker M38 烟测处理 120 帧，自动生成 6 个候选事件和 26 条指标事件记录。11/13 项至少有一个 measured 候选、17/26 条特征实例 measured；FS01-M03 与 FS02-M05 在该短窗口没有 measured 候选，保留真实修复原因而不是显示为 0 分。
- 同一生产 32px ROI 的 97 秒全片报告为 13/13 项至少有一个 measured 候选，403/442 条指标事件实例 measured、39 条 unavailable。两个报告的 registry/events/indicator-features/scores 路径与 SHA-256 均逐源绑定；Python validator 与 Draft 2020-12 Schema 同时验证。Stage-1 `summary.schema.json` 原有的 6 项上限也已移除，改为 registry-driven 正整数目标数，并由真实 13 项 Worker Summary 回归覆盖。
- 机器证据为 `runs/rtmpose-m-halpe26-default-m38-smoke/calculation-readiness.json` 与 `reports/indicator-calculation-readiness-halpe256-full.json`。该文件属于 Pipeline 级派生报告；它自身哈希绑定四个评分源文件，不能替代源 bundle 的跨产物 validator，也不能作为正式评分授权。
- M38 定向验证覆盖报告构建、来源篡改、真实短片/全片、Service 下载和 13 项 Summary Schema；全仓 `scripts/run_tests.ps1` 为 440 tests OK（3 skipped）。主动态报告 54 个去重本地引用均存在，页面及两份计算报告通过本地 HTTP 返回 200。

### M39：每视频输出一组可直接消费的代表性 F2 测量

- Pipeline 新增 `indicator-measurement-portfolio.json`，以本次运行的 registry、events、indicator-features 和 scores 为唯一来源。每个注册表指标最多选一条 `feature_status=measured` 的事件实例，并完整输出 value、unit、feature version、confidence、validity、reason、source frames、视频、Track、事件与模型版本。
- 选择策略固定为 `observation-quality-only-v1.0.0`：依次比较 required-feature 最低置信度、平均置信度、事件置信度和质量状态，再用时间与 ID 确定性打破平局。它明确禁止读取特征值、等级或运动表现，禁止跨模型拼接，因而不会把“挑最好动作”伪装成全视频评分。
- 真实默认 RTMPose Worker M39 烟测处理 120 帧，生成 6 个候选事件、26 条指标实例；11/13 项有代表测量，17/26 条实例 measured。FS01-M03 与 FS02-M05 没有完整候选，报告保留 unavailable 诊断，未填 0。97 秒全片为 13/13 项均有代表测量，403/442 条实例 measured。
- `validate_run.py` 对 Pipeline 声明的 calculation-readiness 与 measurement portfolio 同时验证结构、Summary 声明和四类来源 SHA-256；报告或来源哈希篡改必须 fail closed。Service 白名单和 HTML 报告已暴露两个 JSON，但 grade 与 threshold 继续全部为空。
- M39 全仓 `scripts/run_tests.ps1` 为 445 tests OK（4 skipped）；包含真实 bundle 派生报告哈希篡改回归。主动态报告 57 个去重本地引用均存在，页面、Worker HTML 与短片/全片 portfolio 均由本地 HTTP 返回 200。

### M40：13 项必须能在同一次动作周期内闭合

- 事件审计确认当前 FS01、FS02、FS09 已使用不同窗口，而非同边界重复标签；每个 motion bout 的 FS01 `initiation_ms` 精确等于 FS02 起点，FS02 与 FS09 共享 `peak_speed_ms`，FS09 从 FS02 内部速度峰开始并覆盖 FS02 结束。全片 34/34 组三段可一对一闭合，unmatched event 为 0。
- 新增 `scoring-cycle-measurement-v1.0.0`：要求三段共享视频、主球员 Track 和 detector source，并重算上述时间/阶段不变量。每个周期按 registry 精确检查 13 项及其 required-feature 顺序，不允许从其他周期补入缺项。
- 周期代表选择先最大化同周期 measured 指标数，再最大化通过评分上下文门禁的指标数，最后比较 required-feature 最低/平均置信度和事件置信度；禁止读取特征值、运动表现、grade，禁止跨周期或跨模型拼接。97 秒全片共有 34 个周期，其中 25 个周期在同一次动作内 13/13 项全部 measured；最终代表周期为 13/13 特征完整、12/13 评分上下文通过。
- 真实默认 RTMPose M40 Worker 的 120 帧短片闭合 2 个周期，最佳仅 11/13 特征完整、5/13 评分上下文通过，状态为 `partial_cycle_only`。Pipeline、Summary、Service、HTML 与 `validate_run.py` 均已接入该产物；来源 SHA-256 或阶段证据篡改必须 fail closed。所有 grade/threshold 仍为空。
- M40 全仓 `scripts/run_tests.ps1` 为 451 tests OK（5 skipped）；真实短片/全片 Schema、阶段/特征/来源篡改回归均通过。主动态报告 60 个去重本地引用均存在，页面、Worker HTML 与两份周期 JSON 均由本地 HTTP 返回 200。

### M41：FS02-M02 的“目标方向”必须来自显式参考，不能由运动轨迹自证

- 新增 `scoring-reference-context-v1.0.0`，以视频 ID/SHA-256、FS02 事件码、起止边界和 observation ID 绑定每条目标方向。`accepted` 记录必须给出 image-plane 方向、置信度、观察者和不同身份的 reviewer；`pending/unobservable` 必须保持方向空值并说明原因。
- 运行时用已有 `launch_direction_deg` 与显式目标方向计算 `target_direction_alignment_error_deg`，采用跨 ±180° 的最短角差。该结果是评分上下文特征，不是等级；`grade=null`、`threshold_version=null`，也不把人体运动方向反推为战术目标。
- 事件 ID 在不同任务中变化时，accepted 参考只允许按事件码和完全相同的起止边界唯一重映射；边界变化、同一事件多条 accepted 记录、视频 SHA 不一致、自审、非 image-plane 坐标或非有限值全部 fail closed。pending/unobservable 不会改变分数，边界已变化时可被忽略并要求从当前事件重新生成工作清单。
- Pipeline 请求可通过 `scoring.reference_context` 引用版本化文件；推理前后验证文件 SHA 不变。指标、score evidence、Summary 和 HTML 均保留上下文状态及来源 SHA；Bundle validator 拒绝在 pending/missing 上下文下删除 `tactical_target_direction_not_observed`。
- 97 秒真实视频已生成 34 个 FS02 工作项，当前全部 pending、available=0、grade/threshold=0。真实回放仍为 34 个周期、25 个同周期 13/13 特征完整，代表周期 12/13 评分上下文通过；这诚实保留了最后一个需教练输入的语义阻断。
- 实际 `JobDatabase → PersistentVisionRunner → run_pipeline` GPU 烟测处理 120 帧，当前检测出 3 个 FS02；用本次事件生成的 3 条 pending 参考再次运行后 3/3 精确绑定，13/13 指标均有 measured 候选、grade/threshold=0，Bundle validator passed。
- M41 全仓 `scripts/run_tests.ps1` 为 458 tests OK（6 skipped）。独立 `validate_run.py` 已验证当前 Worker 包（120 帧、9 事件、39 指标记录）和 97 秒全片包（2911 帧、102 事件、442 指标记录）；4 份新增 Schema 实例通过 Draft 2020-12，主报告 65 个本地引用缺失 0，4 个关键 HTTP 入口均返回 200。

### M42：Pose 测量合同不得丢失评分所需的外部上下文

- registry 的 `required_features` 现统一表示完整标定/评分向量；新增可选 `measurement_features` 表示可从当前 Pose/传感器独立计算的子集。除 FS02-M02 外两者相同，旧指标与旧 bundle 仍兼容。
- FS02-M02 的 4 项 Pose 测量为 `body_center_speed_body_s`、`hip_center_relative_to_ankle_support`、`torso_lean_deg`、`launch_direction_deg`；完整评分向量按相同顺序追加 `target_direction_alignment_error_deg`。该角度是版本化纯函数，使用时间戳/事件证据与人工 target，跨 ±180° 取最短角差；0 是有效 0°，缺失永远为 null。
- `indicator-features.jsonl` 同时输出 `feature_status/features`（测量层）和 `scoring_feature_status/scoring_features`（评分层）。Loop、离线 batch、manual-event feature builder、calibration compiler、independent evaluator、Bundle validator 和 HTML 均验证完整评分向量，不能通过丢弃 context-only feature 把 unavailable 重新评分。
- 人工 target semantic 只有 accepted、image-plane 且可与同一人工 event 精确关联时才进入 prepared calibration vector；pending/unobservable、court-plane 无变换、事件边界不匹配或无独立复核均 fail closed。该链路只准备特征，不生成阈值。
- 当前实际 GPU Pipeline 处理 120 帧，耗时 10.788 秒，输出 9 个候选事件和 39 条指标实例；36/39 Pose 测量完整、13/13 指标有 measured 候选、3 个同周期中 1 个为 13/13 测量完整，最佳评分上下文 8/13。3 个 FS02 目标仍 pending，因此三条完整评分向量均 unavailable。
- 当前 97 秒无 GPU 回放复用同一 2,911 帧与 `primary-player-v0.3.0` 时间线，输出 102 个候选事件、442 条指标实例；403/442 Pose 测量完整，评分为 176 calibration_required / 266 unavailable。34 个 FS02 目标全部 pending；grade=0、threshold=0。
- 真值包 manifest/compiled report 已绑定 registry `.15`、full indicator hash `5801ECB4…C7BD` 和 loop v0.6.0；新不可变空标定数据集 `rallymate-calibration-f0179d4e3dc40cb6` 为 `annotation_required`、0 samples、13 个 non-ready prepared 文件、F3=false、无模型/阈值/production 资产。

### M43：原因必须与原始门禁一一对应，上下文特征也必须进入误差预算

- `scripts/audit_scoring_blockers.py` 不再只检查“有活动 flag 却缺 reason”，同时拒绝“没有对应 flag 却多报类型化 reason”。全片当前类型化原因逐项为：身份连续性 43、关键点跳变 201、左右分配 69、目标方向 34、事件运动学覆盖 44、主 Pose 覆盖 22、启动脚侧别 18；每项 supported record count 与 reason count 完全相等。
- 当前完整 scoring vector 的互斥分解为 176 calibration clean、194 complete-vector score-only blocked、3 incomplete-vector/no-score-block、66 incomplete-vector+score-block、3 hard fail。M42 将人工目标方向加入 scoring vector 后，34 条 target pending 应进入“不完整评分向量”，不能继续沿用 M31 的 227 条旧向量完整口径；Pose measurement vector 仍独立为 403/442 measured。
- `evaluate_feature_errors` 新增显式 scoring-context 路径。`target_direction_alignment_error_deg` 使用预测边界/模型 Pose、人工边界/人工校正 Pose和人工 accepted image-plane target 三方构造 production、真值与反事实值，输出 MAE、P95、Bias、有效率、分视角以及 Pose/边界/平滑/缺失四类预算。
- 语义真值必须绑定人工 event ID，并要求有限方向/置信度和独立 annotator/reviewer。缺失、pending、unobservable、自审或非 image-plane 坐标均保持 null/invalid；真实 0° 对齐继续作为合法 0。空真值报告现明确记录 required context feature、semantic record count=0、context truth complete=false，不会把“传了空文件路径”解释为有真值。
- 合成测试只验证数学和契约：包括 0°/90° 方向差、校正 Pose 反事实和缺语义不补 0。它不是教练真值或准确率结果，不推进 F3/F4，不生成阈值或等级。
- M43 全仓 `scripts/run_tests.ps1` 为 464 tests OK（6 skipped）；当前 2,911 帧全片与 120 帧 GPU Worker bundle 均通过跨产物 validator。主报告 63 个去重本地引用缺失 0，主页、阻断审计、空真值报告和人工工作台 HTTP 均为 200。

### M44：每条 unavailable 都必须有明确、当前且可执行的人工动作

- 历史 M31 清单只覆盖旧版完整评分向量的 227 条记录，早于目标方向进入完整 scoring vector。M44 新增 `scoring-truth-action-worklist-v2.0.0`，不覆盖历史产物，直接读取当前 M42 scores 与 M43 blocker audit，并绑定当前 events、summary、registry `.15`、Pose 诊断队列、目标方向文件和连续视频 SHA-256。
- 生成器按 registry 区分 measurement feature 与 context-only feature：人工目标方向缺失进入 `scoring_reference_context`，不会混进 Pose 特征失败；无效 Pose measurement features 按事件合并为一次密集关键点/边界真值动作；score-only 和 hard flags 分别路由到 Pose 诊断、事件阶段或语义任务。
- 当前 266/266 条 unavailable 均至少分配一个动作，共 168 个去重 `event_id × truth_requirement` 工作项和 481 条指标实例×动作关联：Pose 诊断 92、人工关键点/特征 11、目标方向 34、事件/阶段/其他语义 31。全部 92 个 Pose 工作项都能反向关联当前 M42 诊断任务，缺失为 0。
- validator 重算 item union、实例×动作、review type、truth requirement、唯一项、连续优先级和 Pose 任务链接，并要求所有安全字段保持 false。篡改来源哈希、删除某实例动作、伪造 accepted/grade/threshold 或把清单当准确率都会拒绝。
- 浏览器入口 `reports/scoring-truth-action-worklist-halpe256-full-m44/index.html` 使用连续 97 秒视频，可跳转事件并链接当前 Pose 队列、评分真值工作台和目标方向文件。清单仅路由人工任务；完成一项不自动改变 score，全部真值完成但无标定时最高仍为 `calibration_required`。
- M44 全仓 `scripts/run_tests.ps1` 为 470 tests OK（7 skipped）；真实 worklist 通过 Draft 2020-12 Schema，当前全片 bundle 通过 `validate_run.py`。主报告/行动清单/当前 Pose 队列的本地引用为 64/6/1，缺失均为 0；HTTP 入口均为 200。

### M45：人工任务的“已完成”必须从原始证据重算

- 新增 `scoring-truth-action-readiness-v1.0.0`。它读取 M44 worklist、编译后的人工事件/关键点/语义、当前目标方向、完整时间线 Pose truth pack 及相应评测报告，逐项输出 `evidence_satisfied`、`review_in_progress_not_adjudicated` 或 `annotation_required`；不得由清单中的状态字段自行声明完成。
- Pose 浏览器队列的 review CSV 只表示 `review_complete_not_adjudicated`。当前新增 `reports/pose-diagnostic-truth/halpe26-full-m42-v1/`，对 2,911 帧、261 个候选任务提供完整覆盖区间和稀疏阳性真值入口；只有两名独立标注者加独立裁决者形成 accepted 全时间线覆盖，才可满足对应 Pose 工作项。
- 事件/阶段证据使用按视频、事件码和 Track 分组的一对一全局匹配；`Segment IoU >= 0.5` 只用于事件关联协议，不是 A～E 阈值、模型接受阈值或准确率结论。特征项必须在人工边界/关键点重算的误差报告中精确命中当前事件和无效特征，目标方向必须精确绑定当前 FS02 事件并完成独立复核。
- 真实 `reports/scoring-truth-action-readiness-halpe256-full-m45.json` 当前为 `annotation_required`：168/168 工作项、266/266 指标实例均未满足；人工事件、accepted 关键点帧、accepted 语义、完整时间线 Pose 诊断类型和 accepted 目标方向均为 0。完成证据也不会自动重算或解除评分门禁；无标定时最高状态仍是 `calibration_required`。
- 生成器从绑定的 CSV/JSONL 原始证据重新执行事件、特征和 Pose 诊断评测，并核对来源 SHA-256；伪造 `evidence_satisfied`、替换自报评测结果或来源漂移均被拒绝。M45 全仓 `scripts/run_tests.ps1` 为 477 tests OK（8 skipped）；真实报告通过 Python validator 与 Draft 2020-12 Schema，当前全片 bundle 再次通过 `validate_run.py`（2,911 帧、102 事件、2,040 特征、442 指标/评分记录）。主报告、M44 清单和全片 Pose 真值页的 67/6/1 个本地引用均无缺失，四个 HTTP 入口均返回 200。

### M46：共享证据只标一次，所有依赖实例保持可追溯

- M45 的 168 个 work item 中存在大量同源依赖，不能把它们解释为 168 次独立人工标注。新增 `scoring-truth-evidence-acquisition-plan-v1.0.0`，读取并重放 hash-bound M45 报告和 M44 worklist，将共同证据折叠为 87 个 acquisition unit，同时保留全部 168 个 work item、266 个指标实例和依赖映射。
- 4 个完整时间线 Pose 单元最先处理：`keypoint_jump` 同时支撑 54 个 work item / 201 个实例，`left_right_swap` 支撑 28/69，`primary_identity_ambiguity` 与 `source_track_switch` 各支撑 10/43。身份连续性仍必须两个单元同时满足，不能因共享去重而放宽证据。
- 其余 83 个单元由 21 个 accepted 事件边界、9 个阶段、2 个按事件族密集关键点、11 个事件特征真值、6 个支撑/启动侧语义和 34 个目标方向组成。一个 work item 可依赖多个单元；折叠减少重复书写，不减少任何原始真值条件。
- 优先级按受影响指标实例数、work item 数和时间位置确定，只用于安排采集顺序；它不是准确率、风险、动作质量或人工工时估计。`reports/scoring-truth-evidence-plan-halpe256-full-m46/index.html` 提供连续视频跳转，JSON/CSV 提供机器分派。
- 当前 87/87 单元均为 `annotation_required`。强校验器从 M45 源文件重新生成完整计划，能拒绝同步修改状态、优先级、依赖或来源的伪造产物。完成单元仍不会修改 quality/score、生成等级/阈值或推进成熟度。M46 全仓 `scripts/run_tests.ps1` 为 484 tests OK（9 skipped）；真实计划通过 Draft 2020-12 Schema 与 source replay。主报告/M44 清单/全片 Pose 真值/M46 计划的 68/6/1/3 个本地引用均无缺失，五个 HTTP 入口均返回 200。

### M47：一次刷新必须产生不可变、可回放且最后发布的证据快照

- 新增 `refresh_scoring_truth_evidence.py`，依次执行人工 CSV 预检、canonical truth compile、compiled truth 快照、Pose truth 快照、registry/worklist/目标方向快照、事件/特征误差、Pose 诊断误差、M45 readiness、M46 共享计划与总报告重建。`evaluate_scoring_truth.py` 的核心逻辑已抽为可单测的 `build_scoring_truth_evaluation`，CLI 与编排器使用同一实现。
- 每次刷新写入唯一目录 `reports/scoring-truth-refresh/<refresh_id>/`。原始人工 CSV 无错误后才创建运行目录；`refresh-manifest.json` 在全部派生物验证通过后写入，`latest.json` 再用同目录临时文件和 `os.replace` 原子切换。失败运行不更新 latest，也不会被总报告读取。
- refresh bundle 内保存 compiled JSONL、validation、Pose coverage/positives/manifest、registry、worklist 和目标方向快照；未来人工文件改变不会静默改写历史快照。manifest 同时保留原始输入路径与 SHA，用来要求下次变更后重新刷新。
- strong validator 逐文件复算 18 个 artifact SHA，并从快照原始证据完整重放 M45 和 M46；同步篡改 readiness、计划、状态、计数或 latest manifest SHA 都会失败。主报告只通过校验后的 `latest.json` 读取当前刷新。
- 当前已发布 `m47-empty-v2`：除 manifest 外还把原始 Pose coverage/positives CSV 单独纳入 source fingerprint；truth=`annotation_required`、事件/特征=`ground_truth_required`、Pose/M45/M46=`annotation_required`。人工事件、关键点帧、语义和教练标签均为 0，满足 work item 0/168、指标实例 0/266、证据单元 0/87。该命令不调用 score backend，不生成 grade/threshold，不推进 F3/F4。
- M47 全仓 `scripts/run_tests.ps1` 为 490 tests OK（10 skipped）；真实 refresh manifest 通过 Draft 2020-12 Schema、18 个 artifact SHA、source fingerprint 与 source replay，且无 in-progress/failed marker。当前全片 bundle 再次通过 2,911 帧/102 事件/2,040 特征/442 指标与评分记录校验。主报告/M47 首页/M47 共享计划/M44 清单/全片 Pose 真值页的 69/5/3/6/1 个本地引用缺失 0，最新四个 HTTP 入口均为 200。

## 14. 最终验收映射

| 用户验收项 | 对应交付与证据 |
|---|---|
| FS01、FS02、FS09 输出可评测事件区间 | M3；`events.jsonl`、event annotation、Event F1/IoU/Boundary 报告 |
| 当前 13 项输出版本化特征、单位、有效性、证据帧 | M4 + M13；`event-features.jsonl` 和 feature Schema |
| 对人工真值计算事件误差和特征误差 | M3 + M5；`event-evaluation.json`、`feature-evaluation.json`、误差预算 |
| 无真值保持 `calibration_required` | M1 + M6；安全状态机和无标定回归测试 |
| 完成 F4、独立测试、maturity evidence 与受信 promotion/ledger 后，单指标输出 A～E 或 unavailable | M6 + M15；统一 score 契约、阈值/序数后端制品、成熟度证据和生产信任门禁 |
| 每指标可从 F0 逐级推进到 F4且阻断明确 | M1 起贯穿全部里程碑；feasibility 注册表、evidence refs 和晋级门禁 |
| 追溯到视频、Track、事件、特征函数和模型版本 | M2 + M7；原始 Track 映射、source frames、scoring manifest 和哈希 |

## 15. 主要风险与控制

| 风险 | 影响 | 控制 |
|---|---|---|
| 2D Pose 无法恢复真实重心、承重和深度 | 13 项均可能有视角偏差 | 明确 proxy、固定机位分组、view gate、人工真值误差；不冒充生物力学真值 |
| 单踝回退点以及 Halpe26/WholeBody133 细足点都没有地面接触或压力观测 | FS01 离地/落地、FS02 支撑发力/第一步、FS09 制动支撑只能近似 | 使用足部运动/减速代理和质量旗标；不输出真实接触、落地或受力结论 |
| 主球员 Track 严重碎片 | 事件跨 ID、导数跳变 | 稳定身份映射、事件内切换门禁、人工 seed、保留原始 ID provenance |
| 左右点交换和跳点污染二阶导数 | 减速度和角速度失真 | 先诊断后求导、robust smoothing、长缺失拒绝、raw/smoothed 对照 |
| 当前 timestamp 来自 nominal FPS | VFR 视频时序误差 | 特征只消费 timestamp；记录时间源；非可信 timebase 门禁；后续单独评估 PTS 改造，不在本轮重写解码 |
| FS02“目标方向”无法从 Pose 单独知道 | 可能把错误方向判断为正确 | 无人工 target direction 时只报告 observed movement，不给方向正确性结论 |
| 事件规则与特征共用信号产生循环偏差 | 评测过于乐观 | 独立事件真值、边界替换反事实、规则版本冻结、分离 event/feature 数据集 |
| 平滑改善视觉但改变边界/峰值 | 特征偏差和时延 | 保存 raw/smoothed，窗口不跨边界，误差预算单列平滑项 |
| 缺失样本被静默排除 | 有效率虚高 | 同时报 eligible/model/paired valid rate，按缺失原因分组 |
| 小样本教练标签不一致 | A～E 不可标定 | 多标注者一致性、争议复核、样本分布和独立测试；不一致时保持 F2 |
| 数据泄漏 | F3/F4 指标虚高 | 按球员/场次/机位分组；校准集与独立测试集隔离，版本化 manifest |
| 工程完成被误解为正式评分 | 误导用户 | 状态机、注册表证据门禁、报告显著声明、无资产时强制 null grade |

## 15.48 M48：真值证据与标定数据集必须由同一不可变快照交接

- 新增 `scoring-truth-calibration-handoff-v1.0.0`。入口 `scripts/build_scoring_truth_calibration_handoff.py` 只接受通过强校验的 `reports/scoring-truth-refresh/latest.json`；它复制 M47 registry、人工事件、语义、教练标签、truth validation 和 truth manifest，并绑定原 frames/primary timeline SHA。
- 人工事件特征只对 M47 中与绑定视频相同的 accepted manual event 运行 `build_manual_event_features`，事件 ID、边界、phase 和 Track 全来自人工记录；候选事件检测器不参与。其他视频若已有人工事件但尚无绑定 frames/timeline，会保留为缺少测量源，而不会模糊关联候选特征。
- 标定编译器只消费上述 manual-boundary `indicator-features.jsonl`，并按 `video_id + manual event_id + indicator_id` 精确关联。随后对 13 份 prepared 文件逐项执行 `validate_prepared_dataset` 的结构校验和 fit-ready 校验，但不调用拟合器。
- 当前权威交接 `reports/scoring-truth-calibration-handoff/m48-m47-empty-v2/` 为 `annotation_required`：人工事件/人工特征/sample 为 0/0/0，13/13 prepared 文件结构有效，fit-ready 为 0/13。所有拟合前门禁均以 `upstream readiness blockers` 拒绝；candidate、threshold、model、grade、F3/F4 均未生成。
- handoff validator 复算 M47 manifest、全部 source/artifact SHA、filtered manual events、manual-feature lineage、dataset source binding、13 项 prepared 合同、fit-ready replay 和计数。`latest.json` 只有全部验证通过才原子替换；总报告只读取该 latest。全仓回归为 497 tests OK（11 skipped）；实际 Schema 通过 Draft 2020-12，主报告/M48 页面本地引用为 121/4、缺失均为 0，三个 HTTP 入口均为 200。

## 15.49 M49：所有真值视频必须先绑定同一版本的测量源，再进入统一标定组合

M48 只绑定 M47 当时的一段 frames/timeline，因此能够证明单视频交接安全，却不能保证真值包中另外两段视频有同版本的主球员时序，也无法在它们出现 accepted event 后自动重算人工边界特征。M49 新增 `calibration-measurement-sources-v1.0.0`，要求 source spec 精确覆盖 truth manifest 的全部视频，并验证视频 SHA、Pose summary、逐帧索引/时间戳、原生拓扑、模型 SHA 和当前 registry 要求的主球员算法版本。

当前真实 source set 为 `halpe26-m256-primary-v0.3-three-video-v1`：3/3 视频的既有 RTMPose-M Halpe26 256×192 全片帧全部可复用，帧数为 1,441 / 2,911 / 11,516，共 15,868 帧；三段使用同一模型 SHA、配置 SHA 和 Halpe26 原生拓扑。M49 只从这些已有 Pose 帧重建 `primary-player-v0.3.0`，没有重新运行 GPU，也没有运行事件检测器。任何视频缺源、模型/拓扑混用、timeline 与 frame 错位或版本陈旧都会 fail closed。

`scoring-truth-calibration-portfolio-v1.0.0` 同时强校验 M47 latest 与上述 measurement source latest。它按视频筛选 accepted manual event，以原 event_id、人工边界、phase 和 Track 调用 manual-event feature builder，再将三份 indicator-features 精确汇总到一个 13 项 calibration dataset。validator 重放真值刷新、三段 frames/timeline、source snapshots、逐视频人工特征 lineage、dataset sources/outputs、13 份 prepared 合同、fit readiness、状态与计数；修改任一自报状态不能通过。

当前不可变组合 `m49-m47-empty-v2-halpe26-three-video-v1` 为 `annotation_required`：measurement video=3/3、frames=15,868，但 accepted event/manual feature/sample=0/0/0，13/13 prepared 仅结构有效，fit-ready=0/13。安全字段固定 GPU/event detector/fit/candidate/threshold/model/grade/F3/F4 均未发生；这完成的是多视频数据通路，不是事件准确率、特征误差或正式评分证明。

M49 新增定向测试 9/9 通过；全仓 `scripts/run_tests.ps1` 为 506 tests OK（13 skipped optional jsonschema）。两份真实 manifest 已由系统 Python 通过 Draft 2020-12 Schema；主报告/M49 页面分别有 69/3 个去重本地引用且缺失 0，主页、M49、portfolio latest 与 measurement-source latest 四个 HTTP 入口均返回 200。

## 15.50 M50：三段完整视频必须逐项证明计算合同可执行

M49 证明了三段视频可以进入同一真值和标定数据通路，但空真值组合不会运行候选事件层，也不能直接回答“13 项在不同完整视频上是否真的能算出特征”。M50 新增 `multivideo-indicator-calculation-coverage-v1.0.0`：从 M49 已验证的 measurement source 集合读取三段现有 RTMPose-M Halpe26 256×192 Pose 和 `primary-player-v0.3.0` timeline，对每段独立运行当前事件规则、13 项特征库和质量门禁，再对运行包做完整 `validate_run_artifacts` 重放。本轮没有重新执行 GPU 推理。

真实覆盖为 3 段、15,868 帧、546 个候选事件（FS01/FS02/FS09 各 182 个）和 2,366 条指标事件实例。Pose measurement vector 中 2,276 条为 `measured`、90 条为 `unavailable`；13/13 指标在三段视频的每一段中均至少存在一条完整向量。逐指标 measured 数为：FS01-M02 177、M03 172、M04 175、M05 177；FS02-M02 180、M03 178、M04 179、M05 175；FS09-M01 176、M02 162、M03 176、M04 175、M05 174（每项候选实例均为 182）。代表证据只按最早 `start_ms` 和 `event_id` 确定，不读取 feature value、grade 或动作表现。

该结果只证明 F2 计算合同在三段现有视频上可执行，不能把 546 个候选当作 546 个真实动作，也不能把 2,276/2,366 当作准确率。人工事件/阶段/关键点/语义真值仍为 0，Event F1、Segment IoU、Boundary MAE、特征 MAE/P95/Bias 和分视角误差仍不可计算。完整评分状态为 828 条 `calibration_required`、1,538 条 `unavailable`；grade=0、threshold=0，成熟度仍为 F2。机器入口为 `reports/multivideo-indicator-calculation-coverage/m50-halpe26-three-video-current-v1/index.html`。

M50 新增定向回归 6 项；与动态报告回归合计 25/25 通过。全仓 `scripts/run_tests.ps1` 为 512 tests OK（14 skipped optional jsonschema）；真实 coverage 同时通过 Python 强 validator 和系统 Python Draft 2020-12 Schema（0 errors）。主报告/M50 页面本地引用为 70/0、缺失 0，主页、M50 页面和 latest JSON 三个 HTTP 入口均返回 200。

## 15.51 M51：必须把计算缺口、上下文缺口和评分证据门禁分开

M50 的 `feature_status` 是安全运行状态：它同时受原始 Pose 特征有效性和事件 measurement hard gate 影响；`score.status` 又叠加完整评分上下文、身份/关键点/阶段证据、教练标定和独立测试。因此不能把 90 条 feature unavailable 或 1,538 条 score unavailable 直接解释为模型没有计算能力。M51 新增 `multivideo-scoring-readiness-decomposition-v1.0.0`，逐条读取 M50 的 `features`、`scoring_features`、quality gate 和 score，并按“最先阻断层”形成互斥分类。

真实 2,366 条实例中，原始 Pose measurement vector 完整 2,282 条；其中 2,276 条同时通过事件测量门禁。完整 scoring vector 为 2,102 条，839 条通过 scoring gate，二者同时成立的 828 条进入 `calibration_required`。互斥分解为：18 条 measurement hard fail、72 条普通 measurement vector incomplete、180 条 scoring context incomplete、1,268 条 scoring evidence blocked、828 条 calibration-only missing。五类之和严格等于 2,366；前四类合计 1,538，与原 score `unavailable` 精确一致。

该拆解纠正了优化方向：不能通过降低跳点、左右交换、身份连续性、阶段或目标方向门禁来“提高计算率”，也不能给无制动侧、无双脚同步上抬或关键点覆盖不足的特征补 0。真正可讨论的算法缺口应只从 72 条普通测量向量不完整和 18 条 hard fail 中逐事件分析；其余 1,448 条首先需要人工语义/诊断/标定证据。M51 validator 从 M50 coverage 及其三套 run bundle 全量重放状态、计数、原因和哈希，修改自报分组但保持总数不变也会拒绝。入口为 `reports/multivideo-scoring-readiness/m51-halpe26-three-video-current-v1/index.html`。

M51 新增 6 项定向回归，并新增 1 项动态报告安全回归；联合 M51/报告为 26/26 通过。全仓 `scripts/run_tests.ps1` 为 519 tests OK（15 skipped optional jsonschema）。真实 audit 通过 Python 强 source replay 和系统 Python Draft 2020-12 Schema（0 errors）；主报告/M51 页面本地引用为 71/0、缺失 0，主页、M51 页面和 latest JSON 三个 HTTP 入口均返回 200。

## 15.52 M52：差动作的可观察负证据不能被当成缺失值

M51 中 FS09-M02 有 15 条 `braking_ankle_speed_drop_body_s::braking_side_indeterminate`。逐事件回放证明其中 14 条左右踝 event-edge 净速度下降都不为正，但两侧关键点、边界速度和严格为正的局部减速峰均可测；只有 1 条确因事件边界样本不足和低有效率而缺测。指标卡的 E 级语义包含“未观察到制动脚落地”，因此把前 14 条当作 unavailable 会系统性排除差动作，未来也无法用真实教练标签学习区分。

`fs09-pose-proxies-v0.2.0` 保留正净速度下降作为首选侧别信号；只有两侧净下降都不为正时，才用严格为正的局部减速峰确定运动学候选侧。被选侧的净速度变化仍按原值输出，可以为负或 0；时间差仍使用真实 `timestamp_ms` 的局部峰，不填 0、不跨事件借帧。若净变化与局部峰都不存在、或输入不完整，仍输出 null/unavailable。该侧别只表示 2D 踝部运动学代理，绝不宣称真实触地、受力或承重。

同一三视频 15,868 帧无 GPU 重算后，2,366 条实例的 feature measured/unavailable 从 2,276/90 改为 2,290/76；原始 measurement vector 完整从 2,282 提升到 2,296。评分证据门禁保持不变：14 条恢复记录中 9 条仍被身份/关键点等评分证据阻断，只有 5 条从 unavailable 进入 calibration_required，因此总评分状态为 833 calibration_required / 1,533 unavailable。互斥拆解变为 18 measurement hard fail、58 measurement vector incomplete、180 scoring context incomplete、1,277 scoring evidence blocked、833 calibration-only missing。grade=0、threshold=0、13 项仍为 F2。

当前机器入口为 `reports/multivideo-indicator-calculation-coverage/m52-halpe26-three-video-fs09-v0.2-v2/index.html` 和 `reports/multivideo-scoring-readiness/m52-halpe26-three-video-fs09-v0.2-v2/index.html`。真值包、measurement source、refresh、handoff 与三视频 portfolio 已重新绑定 registry `.16`；人工事件/关键点/语义/教练标签和 calibration sample 仍全部为 0，不生成候选标定资产、阈值、模型、等级或 F3/F4。

M52 收口后全仓 `scripts/run_tests.ps1` 为 520 tests OK（15 skipped optional jsonschema）；三套 M52 run bundle 均通过强 validator，分别验证 1,441 / 2,911 / 11,516 帧与 351 / 442 / 1,573 条指标及评分记录。当前 coverage、readiness、requirements、truth refresh 和 calibration handoff 均通过 Draft 2020-12 Schema；主报告与四个 M52 工作页共检查 138 个本地引用，缺失 0。

## 15.53 M53：未形成正向膝伸展也必须保留为可测负证据

M52 后的剩余缺口中有一条 FS02-M03，其左右膝在完整事件内均有合格观测，但峰值伸展速度分别为 −1.76885 与 −0.74599 deg/s。旧 `fs01-fs02-pose-proxies-v0.4.0` 只有在至少一侧为正时才选择测量侧，因而把“没有形成正向膝伸展”的动作表现误记为系统缺测。指标卡低等级语义允许“未观察到伸展支撑”，所以该负向证据必须保留，不能为了得到数值而改成 0。

`fs01-fs02-pose-proxies-v0.5.0` 保持正向伸展候选优先；仅当两侧都完整可见、两侧峰值都不为正且不存在精确平局时，选择数值较大的非正峰作为测量候选。`drive_side_code` 仍为 0，绝不宣称真实支撑脚；速度和实际 `timestamp_ms` 时差原样输出，provenance 固定声明这不是触地、受力或因果发力。缺任一侧、精确平局或必要时序缺失继续返回 null/unavailable。

同一 3 段、15,868 帧、546 个候选事件和 2,366 条实例无 GPU 重放后，raw measurement vector 完整/不完整为 2,297/69，运行时 feature measured/unavailable 为 2,291/75；评分状态为 834 `calibration_required` / 1,532 `unavailable`。互斥拆解为 18 measurement hard fail、57 measurement vector incomplete、180 scoring context incomplete、1,277 scoring evidence blocked 和 834 calibration-only missing。变化仅发生在长视频 `8d7754…` 的 `fs02-119-df73ac9d7ea1`，其保留 −0.74599 deg/s 与 250 ms 的证据值并进入 calibration_required。

当前 registry 为 `pose-wave-2026-08-22.17`。三视频 coverage/readiness、truth refresh、measurement source、handoff、portfolio、indicator requirements 与总报告均已重新绑定；人工事件、关键点、语义和教练标签仍为 0，grade/threshold 仍为 0，13 项继续保持 F2。剩余 75 条 feature unavailable 主要对应真实关键点/时序观测不足，未补零、未跨事件借帧，也没有降低质量门禁。

M53 最终全仓回归为 521 tests OK（15 skipped optional jsonschema）。三套真实 run bundle 分别通过 1,441 / 2,911 / 11,516 帧、81 / 102 / 363 事件和 351 / 442 / 1,573 条指标/评分记录的跨产物强校验；总报告 129 个本地引用缺失 0，主页、coverage、readiness 和 portfolio 四个 HTTP 入口均返回 200。

## 15.54 M54：平滑影响先做可重建性审计

M54 不修改事件、特征生产值、质量门禁或评分状态，只修正误差预算的反事实重放语义。FS09 v0.2 在双侧 event-edge 净速度下降均不为正时，会用严格为正的局部减速峰选择候选侧；评测层现直接复用同一 `braking_side_from_speed_drops` 纯函数，并从序列化 raw 踝速度按实际 `timestamp_ms` 计算无额外平滑的局部减速。`braking_side_code=0` 保持合法诊断值，不能再被当成缺失。

新增 `smoothing-counterfactual-coverage-v1.0.0`、CLI、严格 Python validator 和 Draft 2020-12 Schema。报告绑定 registry `.17` 与三份 M53 `features.jsonl` SHA，只审计 registry 52 个 required feature 中的 51 个 Pose 特征；需要人工战术上下文的 `target_direction_alignment_error_deg` 单独排除。10,192 条相关记录中 9,971 条为有效数值，5,695 条能无歧义重放 raw/no-smoothing 对照，覆盖率 57.115635%；27/51 特征全量可重放，24/51 当前 payload 不足，0 个完全未观察。

FS09 `braking_side_code` 和 `braking_ankle_speed_drop_body_s` 均从旧评测缺口恢复为 178/178。两个派生峰值时序差仍明确返回 `derived_timing_series_counterfactual_not_reconstructable`；其他复合足部、方向与阶段特征也按逐项 reason 保持 null，不猜序列、不补 0。该覆盖只表示单因素反事实可执行，不是人工真值 MAE/P95/Bias，也不是可加和误差预算；13 项仍为 F2，grade=0、threshold=0。

M54 最终相关回归 51/51、全仓 527 tests OK（15 skipped optional jsonschema）。机器报告通过 Python 强 validator 与 Draft 2020-12 Schema（0 errors）；总报告 77 个唯一本地引用缺失 0，主页和 M54 JSON 两个 HTTP 入口均返回 200。

## 15.55 M55：FS02-M03 的平滑反事实必须复用生产侧别与实际时间戳

M55 继续保持事件、生产特征值、质量门禁和评分状态不变，只扩充可审计的 no-extra-smoothing 反事实。评测层从序列化 raw 左右膝屈曲序列按实际 `timestamp_ms` 计算负导数峰，并复用生产 `drive_side_from_knee_extension_peaks` 与 `drive_measurement_candidate_side`；脚部时点从 raw 左右脚垂直速度峰取得。因此 `drive_side_code`、`support_knee_extension_velocity_deg_s` 和 `support_drive_to_moving_foot_rise_proxy_ms` 均达到 179/179 精确回放。

`hip_acceleration_along_launch_direction_body_s2` 的现有 payload 只有投影后的摘要，没有足以重新计算 raw 启动方向的髋位置/速度原语。该项继续以 `no_semantically_matching_raw_smoothed_series` 保持 null，禁止用生产平滑方向替代 raw 方向，也不从 dict 猜测首个序列。三视频总覆盖由 5,695/9,971 提升至 6,232/9,971（62.501254%）；51 个 Pose required feature 中 30 个全量可重放、21 个不可重建。

`smoothing-counterfactual-coverage-v1.1.0` 为每个特征新增单位和影响统计，并保留 v1.0 历史报告的 Schema 兼容读取。真实 FS02-M03 中，drive side 的 raw/production 一致率为 144/179（80.446927%）；膝伸展速度的平均绝对差/P95 为 98.28672910/321.77548943 deg/s；膝峰到移动脚峰时差的平均绝对差/P95 为 229.73743017/963.10 ms。它们仅表示平滑敏感性，不是对人工真值的 MAE/P95，不能据此判断更准确、设计阈值或晋级 F3。

M55 定向回归 34/34、全仓 530 tests OK（15 skipped optional jsonschema）。当前与历史两份审计报告均通过 Draft 2020-12 Schema；M55 强 validator 会重新读取绑定 registry 和三份 source features、重建全报告并逐字段比对。13 项保持 F2，人工真值仍为 0，grade=0、threshold=0。

## 15.56 M56：启动方向反事实必须使用二维 raw 髋轨迹和环形角差

M56 优先处理原始六项中的 FS02-M02。`launch_direction_deg` 的现有 payload 已同时保存 raw/smoothed 髋中心二维轨迹，因此评测层可以把 raw 轨迹、逐帧 `timestamp_ms` 和完整事件索引直接传给生产 `launch_direction_from_hip_motion`。该函数与生产一致地组合事件边缘位移、中位速度和实际时长；二维分量缺失、维数错误、时间戳不递增或方向不确定时保持 null，不填 0、不假设 FPS。

三视频 182 条有效启动方向全部可重放，总覆盖由 6,232/9,971 提升为 6,414/9,971（64.326547%），51 个 Pose required feature 中 31 个全量可重放、20 个不可重建。方向差不能使用普通线性相减：v1.2 把 `launch_direction_deg` 和 `hip_center_motion_direction_deg` 固定为跨 ±180° 的最短环形差，并加入 −179°/179° 应为 2° 而非 358° 的回归测试。

真实启动方向的 production/raw 平滑敏感性 mean/P95 absolute circular difference 为 4.57920293°/10.74166982°，signed mean 为 −1.72698427°；最大绝对差 160.51513435°，绑定视频 `850cb0006b406c7176eeda8d711cd065`、事件 `fs02-005-df7ae55d5371`。最大差异是人工复核优先级，不是误差、异常动作或模型优劣结论。目标方向对齐特征的人工真值评测现在可复用 raw launch direction 计算 smoothing budget，但当前人工 target、人工事件和校正关键点仍为 0。

M56 没有修改生产特征、事件、质量门禁、registry 或 score。`hip_acceleration_along_launch_direction_body_s2` 仍缺 raw 方向原语；`stability_duration_ms` 的历史 payload 将已平滑速度放在 raw 字段，评测器明确拒绝把它当作 no-smoothing 证据。M56 定向联合回归 46/46、全仓 533 tests OK（15 skipped）；M54/M55/M56 三份报告均通过 Draft 2020-12 Schema，M56 通过完整 source replay。13 项仍为 F2，grade=0、threshold=0。

## 15.57 M57：稳定持续时间必须保留可重放的 raw 与 smoothed 双序列

FS09-M04/M05 的 `stability_duration_ms` 依赖事件内髋中心速度和肩髋绝对角速度共同进入自适应稳定包络。旧 payload 的 `raw_value.speed` 实际保存了额外平滑后的速度，因此无法构造诚实的 no-extra-smoothing 反事实。M57 将该特征升为 `0.2.0-provisional-envelope-evidence`：raw payload 保存平滑前速度与绝对角速度，smoothed payload 保存生产使用的两条序列、稳定掩码和自适应上限；缺失、错位、非严格递增时间戳继续 fail closed。

生产计算与误差评测统一调用纯函数 `stability_duration_from_series(timestamp_ms, speed_body_s, absolute_angular_velocity_deg_s)`。该函数不假设固定 FPS，使用实际时间戳和中位采样间隔计算最长连续稳定区间。三视频无 GPU 重放覆盖 15,868 帧、546 个候选事件、2,366 条指标实例；与 M53 逐条对照后，10,920 条 feature 记录的 value、valid、reason、source_frames 和 unit 均未变化，仅 182 条 `stability_duration_ms` 的 feature version/证据合同升级。

M57 平滑反事实报告绑定三份新 `features.jsonl` SHA。177 条有效稳定持续时间全部可重放，整体覆盖从 6,414/9,971 提升到 6,591/9,971（66.101695%），51 个 Pose required feature 中 32 个全量可重放、19 个不可重建。稳定持续时间的 mean/P95/signed/max difference 为 34.05084746/124.2/13.85875706/251.0 ms；最大差异定位到视频 `8d7754d0de6d315674013d5b69a0b6ba`、事件 `fs09-075-73bc19a50f66`。这些值是平滑敏感性，不是人工真值 MAE、准确率或评分等级间距。

M57 重新发布当前多视频覆盖与就绪度 `m57-halpe26-three-video-stability-evidence-v1`：仍为 2,291/2,366 feature measured、834 `calibration_required` / 1,532 `unavailable`，互斥阻断计数保持 18/57/180/1,277/834，说明本里程碑没有放宽事件或评分门禁。人工事件、关键点、语义和教练标签仍为 0，13 项继续保持 F2；不生成 A～E、阈值、F3/F4 或准确率结论。

## 15.58 M58：沿启动方向的髋加速度必须同时重放方向与二阶运动学

FS02-M02 的 `hip_acceleration_along_launch_direction_body_s2` 不是单一标量序列的 peak：启动方向由事件内髋中心位移与速度共同组成，加速度再投影到这个方向。只序列化二维加速度、却沿用 production smoothed 方向，会把两条不同反事实链混在一起。M58 因此在不改变特征值语义和既有 v0.5 feature contract 的前提下，新增 `raw_and_smoothed_hip_position_acceleration_series_v1` 证据合同；raw/smoothed 都保存二维髋位置、body-normalized 二维加速度和投影结果。

新增纯函数 `hip_acceleration_along_launch_direction_from_series`，内部调用生产 `launch_direction_from_hip_motion`，再按同一 event indexes 投影并选择 peak。它校验二维形状、长度、严格递增 `timestamp_ms`、索引范围和对齐；任一条件不满足时返回 unavailable，不用 0 或 production summary 补齐。误差评测只在 raw position/acceleration 的 timestamp 与 source-frame key 精确一致、且对应 smoothed 语义同名时调用该纯函数。

三视频无 GPU 重放后，10,920 条 feature 记录的 value、valid、reason、source_frames、unit 和 feature_version 与 M57 完全一致；只有 182 条目标特征的 raw/smoothed evidence payload 增加。该特征 182/182 可重放，整体覆盖由 6,591/9,971 提升至 6,773/9,971（67.926988%），33/51 个 Pose required feature 全量覆盖、18/51 不可重建。

平滑前后 hip acceleration 投影的 mean/P95/signed/max difference 为 121.24764438/107.12051454/−115.07786512/9,311.43901824 body/s²；最大差异绑定视频 `850cb0006b406c7176eeda8d711cd065`、事件 `fs02-029-3ee8dec71927`。均值高于 P95 是由该极端二阶导数个例造成，不是统计错误；它反而说明人工关键点和事件边界误差预算必须优先审查该事件。M58 当前 coverage/readiness 计数继续保持 2,291/75 和 834/1,532，grade=0、threshold=0、13 项仍为 F2。

M58 最终全仓回归为 540 tests OK（15 skipped optional jsonschema）；M54～M58 报告、M58 coverage/readiness 均通过 Draft 2020-12 Schema，主页 130 个本地引用缺失 0，主页与 M58 JSON 的本地 HTTP 均返回 200。`compileall` 和 `git diff --check` 通过。

## 15.59 M59：FS09 阶段时差必须由可追溯序列重放

M58 之前，`braking_ankle_slowdown_to_hip_deceleration_ms` 与 `hip_deceleration_to_double_support_proxy_ms` 虽按真实 `timestamp_ms` 计算生产值，但证据 payload 把生产使用的 `hip_slowdown` 错标为另一条 `hip_deceleration` 序列，并且没有完整保存左右踝 slowdown 或组成低运动代理的左右踝速度。因此误差评测只能返回 `derived_timing_series_counterfactual_not_reconstructable`，不能从摘要猜时点。

M59 新增两个公开纯函数：一个从左右踝速度、左右踝 slowdown 与髋 slowdown 选择制动侧并计算峰值时差；另一个从左右踝速度构造事件内同步低运动代理，并计算髋 slowdown 峰到该代理起点的时差。两者都校验一维对齐、严格递增时间戳、事件索引范围与顺序；错位、重复/逆序时间或非法索引均 fail closed。生产路径与反事实评测复用同一聚合函数，不假设固定 FPS，不观测触地、受力或承重。

证据契约新增 `fs09_phase_timing_raw_speed_and_prepared_series_v1`。raw 保存左右踝速度和髋速度；prepared/smoothed 保存生产实际使用的左右踝速度、左右踝 slowdown、髋 slowdown 与低运动 mask。FS09 特征值语义和 `fs09-pose-proxies-v0.2.0` 保持不变。三视频 M58→M59 的 10,920 条记录中，value、valid、reason、source_frames、unit、feature_version 和 raw_value 全部逐条相同；只有目标两项共 364 条 smoothed/evidence payload 改变。

M59 无 GPU 重放后，两项分别达到 178/178 与 176/176 可重建；总覆盖从 6,773/9,971 提升到 7,127/9,971（71.477284%），35/51 个 Pose required feature 全量可重放、16/51 仍不可重建。braking→hip 与 hip→double-support 的 raw-vs-smoothed P95 差为 1,137.45 / 916.25 ms，最大差为 2,791 / 2,125 ms。它们是当前代理时点对平滑的敏感性，不是人工边界 MAE、动作准确率或等级间差异。

三视频 calculation coverage/readiness 保持 2,291 measured / 75 unavailable、834 calibration_required / 1,532 unavailable，互斥拆解仍为 18 / 57 / 180 / 1,277 / 834；grade=0、threshold=0、13 项仍为 F2。人工事件、关键点、语义和教练真值仍为空，因此 M59 不生成 A～E、不推进 F3/F4，也不放宽任何质量门禁。

M59 最终全仓回归为 546 tests OK（15 skipped optional jsonschema）；三套 run bundle 分别通过 1,441 / 2,911 / 11,516 帧的跨产物校验。M59 smoothing、coverage、readiness 通过 Draft 2020-12 Schema，主页 130 个本地引用缺失 0，主页与 M59 JSON 的本地 HTTP 均返回 200；`compileall` 和 `git diff --check` 通过。

## 15.60 M60：FS01-M03 四个必需特征形成完整平滑反事实证据族

FS01-M03 依赖双脚上抬幅度、左右上抬峰时差、同步上抬持续时间和髋中心向上速度。M59 以前，这四项生产值虽然都使用真实 `timestamp_ms`，但证据载荷不完整：持续时间的 `raw_value` 实际来自平滑后位置，其他三项缺少与 raw 同名、同时间网格的 production prepared 序列，因而不能诚实重放 no-extra-smoothing 对照。

M60 新增四个可单测纯函数，并由生产计算与误差评测共同调用。所有函数严格校验二维位置/速度形状、长度、严格递增时间戳、事件索引范围与顺序；错位或非法输入返回 unavailable，不补 0、不按固定 FPS 推算。持续时间继续只有在双脚完整观测且时间最大间隔不超过 160 ms 时，才允许把“没有同步半峰区间”记作合法 0；缺失仍为 null。

证据合同为 `fs01_m03_raw_and_prepared_kinematic_series_v1`：raw 保存左右脚位置或速度、髋位置；prepared/smoothed 保存对应同名序列及上抬幅度、速度或同步 mask。特征值语义和 `fs01-fs02-pose-proxies-v0.5.0` 不变。M59→M60 的 10,920 条真实 feature 记录在 event/Track、value、unit、valid、reason、source_frames 和 feature_version 上逐条相同；raw 只改变旧语义错误的 182 条持续时间记录，smoothed/evidence 只改变目标四项共 728 条记录。

三视频无 GPU 重放后，四项分别达到 176/176、176/176、173/173、175/175 可重建，总覆盖从 7,127/9,971 提升为 7,827/9,971（78.497643%），39/51 个 required Pose feature 全量可重放、12/51 仍不可重建。四项 raw-vs-smoothed P95 差依次为 0.45801831 body、625 ms、84 ms、6.21942596 body/s；最大差分别为 14.04118835 body、792 ms、167 ms、37.96129254 body/s。它们量化平滑敏感性，不是人工关键点 MAE、事件准确率或动作等级差异。

M60 calculation coverage/readiness 仍为 2,291 measured / 75 unavailable、834 calibration_required / 1,532 unavailable，互斥拆解保持 18 / 57 / 180 / 1,277 / 834；grade=0、threshold=0、13 项仍为 F2。人工事件、关键点、语义和教练真值仍为空，因此不推进 F3/F4、不放宽 quality gate，也不把 2D 上抬代理称为真实腾空或地面接触。

M60 最终全仓回归为 553 tests OK（15 skipped optional jsonschema）；三套 run bundle 分别通过 1,441 / 2,911 / 11,516 帧及 351 / 442 / 1,573 条指标记录的跨产物校验。M60 smoothing、coverage、readiness 通过 Draft 2020-12 Schema，主页 130 个本地引用缺失 0，主页与 M60 JSON 的本地 HTTP 均返回 200；`compileall` 和 `git diff --check` 通过。

## 15.61 M61：FS01-M04 三项减速后稳定代理形成共享锚点和可重放证据

FS01-M04 的双脚垂直减速时差、减速后站距和髋中心横向波动原先都能产生 F2 数值，但生产路径与误差评测没有共享同一个事件内减速锚点函数，站距与髋波动的 raw payload 也只保存派生摘要，无法诚实重建 no-extra-smoothing 反事实。M61 新增三个公开纯函数，统一从左右脚二维位置按真实 `timestamp_ms` 求垂直速度和局部减速锚点；站距在共同锚点后取中位数，髋横向波动按同一锚点后的髋 x 计算总体标准差。所有函数严格校验时间网格、二维形状、事件索引和 body scale，缺失或错位继续返回 unavailable。

右边界截断策略保持原生产语义：只有共同减速锚点后的髋中心恰好剩一个有效样本时，才允许在事件内部追加最近的一个有效前样本，且时间差必须不超过 160 ms；禁止跨事件借帧、固定 FPS 推算或补 0。证据合同 `fs01_m04_raw_and_prepared_slowdown_series_v1` 保存 raw/smoothed 同名左右脚位置、髋位置，以及生产侧垂直速度或站距序列。评测器先要求 raw 与 smoothed 的 timestamp/source-frame 网格精确一致，再调用同一纯函数。

M60→M61 的三视频 10,920 条 feature 记录在 event/Track、value、unit、valid、reason、source_frames 和 feature_version 上全部一致。raw payload 只扩展站距与髋横向波动各 182 条，共 364 条；smoothed/evidence 只扩展目标三项各 182 条，共 546 条。三视频 9,971 条有效 Pose 特征记录中 8,353 条可重放，覆盖率 83.772942%；41/51 个特征全覆盖、1 个部分覆盖、9 个仍不可重建。减速时差为 176/176、髋横向波动为 175/175；减速后站距为 175/176，唯一一条 raw 序列无法形成减速锚点，继续输出 counterfactual unavailable。三项 P95 raw-vs-smoothed 差为 440 ms、1.10408142 body 和 0.20364359 body；这些只表示平滑敏感性，不是人工真值 MAE 或等级差异。

当前三视频 calculation coverage/readiness 仍为 2,291/75 feature measured/unavailable、834/1,532 score calibration_required/unavailable，互斥拆解仍为 18/57/180/1,277/834；grade=0、threshold=0、13 项保持 F2。M61 全仓回归为 560 tests OK（15 skipped optional jsonschema）；三套运行包分别通过 1,441/2,911/11,516 帧、351/442/1,573 条指标记录验证，M61 smoothing/coverage/readiness 三份机器报告通过 Draft 2020-12 Schema。主页 130 个引用、77 个去重本地目标缺失 0，主页与 M61 JSON 的本地 HTTP 均返回 200，`compileall` 和 `git diff --check` 通过。人工事件、关键点、语义、教练标签和独立测试仍为空，因此不晋级 F3/F4，不生成 A～E 或经验阈值。

## 15.62 M62：FS02-M04 四项启动脚代理共享完整事件运动学合同

FS02-M04 的启动侧、启动脚峰值速度、相对位移和运动持续时间原先都能生成 F2 值，但 raw/smoothed payload 没有同时保存髋中心、左右脚位置和左右脚速度，评测层无法在不借用 production side 的条件下重建完整反事实。M62 新增公开纯函数 `launch_foot_event_kinematics_from_series`：它在同一真实 `timestamp_ms` 网格和事件索引上计算启动方向、左右脚沿该方向的事件边缘位移、启动侧、选中脚峰值速度、相对位移、正向半峰运动区间和持续时间。输入长度、二维形状、时间顺序、索引或 body scale 非法时直接 unavailable，不按固定 FPS 推算，也不把缺失写成 0。

证据合同 `fs02_m04_raw_and_prepared_launch_kinematics_v1` 对四项都保存同一组五条原语序列：`hip_position`、`left_foot_position`、`right_foot_position`、`left_foot_velocity`、`right_foot_velocity`。误差评测必须核对 raw/smoothed 的 timestamp 与 source-frame key 完全相同，再调用生产纯函数；禁止复制 production side、从 dict 猜第一条序列或把“脚运动代理”称为真实离地/触地。M61→M62-v2 的 10,920 条真实记录在 event/Track、value、unit、valid、reason、source_frames 和 feature_version 上全部一致；raw 与 smoothed/evidence 都仅改变四个目标特征各 182 条，共 728 条，没有非目标 payload 漂移。

三视频 9,971 条有效 Pose 特征记录中 9,068 条可重放，覆盖率为 90.943737%；42/51 个 required Pose feature 全覆盖、4 个部分覆盖、5 个仍不可重建。`launch_side_code` 为 179/179，raw/production 侧别一致 172/179（96.089385%）；峰值速度、相对位移和持续时间分别为 179/180、178/179、179/180。三条 raw 轨迹不能形成正向启动侧时继续返回 unavailable，不用 production side 回填。后三项 P95 raw-vs-smoothed 差为 47.70210309 body/s、0.69326041 body、171 ms；最大差为 574.74497194 body/s、2.59551567 body、375 ms。这些只量化平滑敏感性，不是关键点真值误差、动作准确率或等级间距。

M62 三视频 calculation coverage/readiness 仍为 2,291/75 feature measured/unavailable、834/1,532 score calibration_required/unavailable，互斥拆解保持 18/57/180/1,277/834；grade=0、threshold=0、13 项仍为 F2。全仓回归为 567 tests OK（15 skipped optional jsonschema）；三套 M62-v2 bundle、smoothing/coverage/readiness Schema、主页本地引用和 HTTP 入口均通过最终校验。人工事件、关键点、目标语义、教练标签和独立测试仍为空，因此本里程碑不修改事件检测、quality gate、F3/F4 或任何 A～E 标准。

## 15.63 M63：FS02-M05 五项第一步阶段代理完成平滑反事实闭环

FS02-M05 的启动脚速度下降、第一步位移、步后髋方向一致性、启动脚减速到髋方向建立时差和步后站距原先都有 F2 生产值，但 raw/smoothed 证据没有共享完整的髋与双脚位置/速度，也没有明确冻结事件检测器给出的第一步减速阶段。M63 新增公开纯函数 `first_step_phase_kinematics_from_series`，严格输入同一 `timestamp_ms` 网格上的髋位置/速度、左右脚位置/速度、事件索引、body scale 和版本化 `first_step_slowdown_proxy_ms`；时间、形状、索引、scale 或阶段不合法时 fail closed。

五项统一声明 `fs02_m05_raw_and_prepared_first_step_phase_kinematics_v1`。raw 与 prepared 都保存 `hip_position`、`hip_velocity`、`left_foot_position`、`right_foot_position`、`left_foot_velocity`、`right_foot_velocity`；评测层必须固定 production 事件阶段，只替换 raw/平滑序列，禁止 raw 重放自行选择另一阶段。该阶段和所有输出都只是 2D Pose 运动学代理，不观测真实第一步落地、脚底接触、冲击或地面作用力。

M62-v2→M63 的 10,920 条真实 feature 记录在 event、Track、value、unit、valid、reason、source_frames 和 feature_version 上逐条一致；raw 与 smoothed/evidence 分别只改变五个目标特征各 182 条，共 910 条，非目标 payload 变化为 0。速度下降、第一步位移、髋方向一致性、阶段到方向建立时差和步后站距分别达到 179/180、179/180、180/180、180/180、179/179 可重放。前两项的同一条 raw 轨迹无法形成正向启动侧，因此诚实保持 unavailable，没有从 production side 倒填。

三视频 9,971 条有效 Pose 特征记录中 9,965 条可重放，覆盖率为 99.939825%；45/51 个 required Pose feature 全覆盖、6 个部分覆盖、0 个完全不可重放。五项 P95 raw-vs-smoothed 差依次为 50.75936567 body/s、0.52313353 body、0.22365749、84.75 ms、0.41392841 body。它们只量化平滑敏感性，不是人工关键点 MAE、事件边界 MAE、动作准确率或等级间距。

M63 calculation coverage/readiness 继续保持 2,291/75 feature measured/unavailable、834/1,532 score calibration_required/unavailable，互斥拆解为 18/57/180/1,277/834；grade=0、threshold=0、13 项保持 F2。全仓 574 tests OK（15 skipped optional jsonschema）。三套 M63 bundle、平滑/覆盖/就绪机器报告和动态页面继续使用严格 source replay；人工事件、校正关键点、目标语义、多教练等级与独立测试仍为 0，因此本轮不推进 F3/F4，不生成正式 A～E。

## 15.64 M64：把计算覆盖、平滑敏感性和真值误差汇入同一 F2 就绪矩阵

M63 之前，三视频 calculation coverage、平滑反事实报告和 `evaluate_scoring_truth` 各自独立存在。维护者可以看到 13 项都有 measured 候选，也可以看到 9,965/9,971 条平滑对照可重放，但没有一份机器报告逐指标说明 Event F1、特征 MAE/P95/Bias、Pose/事件边界/真值条件平滑/缺失值误差和外部等级间距判断分别是否具备。这样容易把“工程证据完整”误读为“误差已经小于等级间距”。

M64 新增 `f2-error-budget-readiness-v1.0.0`。构建器强绑定当前 registry、M63 smoothing report、M63 calculation coverage、三套 run 的 frames/primary timeline/events，以及人工 events/keypoints/semantics；构建和验证都会重新执行三段真值评测，视频、指标、51 个 Pose 特征或任一 SHA 不一致即拒绝。报告不读取自报的 ready 布尔值，也不把 candidate event 当真值。

当前矩阵确认 10/13 指标的全部 required Pose feature 平滑反事实完整，FS01-M04、FS02-M04、FS02-M05 因少量 raw 轨迹无法形成合法锚点/侧别而为 partial；所有 13 项仍在每段视频至少有 measured 候选。与此同时，人工事件/关键点/语义均为 0，因此逐项 Event F1/IoU/Boundary MAE、特征 MAE/P95/Bias、Pose error、event-boundary error、truth-conditioned smoothing error、missing-value error impact 和外部 grade-gap assessment 都明确为 `ground_truth_required` 或 `external_assessment_required`。

M64 的 `indicators_ready_for_f2_to_f3` 固定为真实证据重放结果，当前 0/13；安全契约强制 `maximum_maturity_claim=F2`、grade/threshold/accuracy/promotion claims 全 false。该矩阵是 maturity-evidence 之前的就绪汇编，不替代预注册协议、人工复核或顺序 F0→F4 证据链。机器报告为 `reports/f2-error-budget-readiness-m64.json`，使用说明为 `docs/F2_ERROR_BUDGET_READINESS.md`。

## 15.65 M65：三视频评分阻断必须精确归因并转成全时间线真值任务

M65 将 `multivideo-scoring-readiness-decomposition` 升级到 v1.1.0。它在读取当前三视频 coverage 后，强制 `indicator-features` 与 `scores` 的完整评分向量、quality gate、状态和 reason codes 精确一致；每个类型化原因必须能反查到活动 scoring block flag，额外或错误的身份、跳点、左右交换、目标方向或阶段原因都会 fail closed。

在 1,277 条完整评分向量但评分证据未验证的实例中，关键点跳变参与 1,025 条且是 645 条的唯一阻断；左右交换参与 482 条且是 173 条的唯一阻断。该排序仅用于人工复核资源分配，不是诊断准确率，也不自动修改质量策略。三段完整视频现已生成 134/261/1,121 个去重 Pose 诊断候选任务，共 1,516 项，并各自生成覆盖区间与稀疏真阳性的空白 truth pack。

当前三视频 coverage 均为 0，Pose 诊断 precision/recall/F1 为 null，聚合策略审查状态为 `annotation_and_protocol_required`。任何门禁解除都必须经过双人独立标注、第三方裁决、结果揭示前冻结的外部接受协议和独立人工策略发布；即使解除，也只会把符合条件的实例推进到 `calibration_required`，不会产生 A～E。

M65 最终验证为全仓 581 tests OK（16 skipped optional jsonschema）；另用 Draft 2020-12 校验器验证 11 份 M65 机器文档，0 error。三条 review queue、三份 truth pack、最新多视频拆解均通过磁盘源文件 SHA 重放；主页与 4 个 M65 HTML 页面共检查 140 个本地引用，缺失 0，主页、拆解、三条复核页和策略 JSON 的本地 HTTP 均返回 200；`compileall` 与 `git diff --check` 通过。

## 15.66 M66：小 ROI 只能作为可观测性恢复候选，必须保留原测量门禁

M66 对三段当前 M63 完整视频的 75 条 operational measurement unavailable 做 source replay。当前 2,366 条指标实例中，Pose required-feature 向量为 2,297 complete / 69 incomplete；再叠加原事件与身份测量门禁后，实际为 2,291 measured / 75 unavailable。18 条 measurement hard fail 中，6 条只被 hard gate 阻断，12 条同时存在不完整向量。报告和代码必须始终分开这两种口径，不能把 feature-vector complete 直接称为 measured。

缺口预检只允许重跑已经存在主球员检测、仍在原 `max_players=2` 调度范围、且唯一原因是生产 32px ROI 尺寸保护的帧；它不是按结果挑模型，也不改变候选事件、Track、模型权重或质量策略。视频 1 没有合格目标帧；视频 2/3 分别有 131/18 帧。相同 RTMPose-M Halpe26 权重在实验 8px 门槛下对 149/149 帧产生 Pose，固定边界重算恢复 22 条 Pose 特征向量、回归 0 条。保留原始事件/身份测量门禁后，只有 20 条 operational measurement 从 unavailable 变为 measured；两条完整向量仍因原 hard gate 保持 unavailable。

因此当前生产事实继续是 2,291 measured / 75 unavailable；实验投影仅为 2,311 / 55，不能写入生产 run、标定数据集或评分结果。18 条 measurement hard fail 不变，投影后仍有 37 条非 hard-fail 特征缺口。生产最小 ROI 继续为 32px，自动 fallback 为 false；149/149 表示“实验模型有输出”，不是关键点准确率、PCK、MAE 或模型优劣。正式路由变更必须先完成人工小框关键点真值、分视角误差、预注册接受协议和独立发布审核。

M66 增加 v1.1 缺口审计、v1.1 小 ROI 实验、三视频聚合契约和两段 H.264 动态 A/B。聚合器重新读取并校验全部源 SHA，重放固定边界 vector/operational transitions，要求有合格目标的每段视频都提供实验、目标为 0 的视频不得伪造实验，并拒绝回归、自动 fallback、门禁变更、accuracy/grade/threshold/F3/F4 声明。机器入口为 `reports/measurement-recovery-m66/multivideo-small-roi-v1/`；两段动态视频位于对应 `small-roi-v1.1/<video_id>/` 目录。

M66 最终验证：全仓 588 tests OK（16 个可选依赖跳过）。三份 gap audit、两份小 ROI 实验和一份聚合报告均通过 Draft 2020-12 Schema；两段视频分别为 H.264 390 帧/13 秒与 198 帧/8.25 秒，首/中/末解码通过。人工事件、关键点、语义和教练真值仍为 0，13 项保持 F2，grade=0、threshold=0。

## 15.67 M67：扩大 ROI 上下文不能假设单调增益，组合策略必须实际重算

M67 保持 RTMPose-M Halpe26 256×192 权重、检测、Track、主球员时间线、候选事件、特征公式与质量门禁不变，只对已有 Pose 但存在特征观测缺口的帧将 ROI margin 从 0.15 改为 0.30。三段真实视频共 410 个目标帧，410/410 产生实验 Pose；固定相同边界重算后，特征向量恢复 14 条、回归 1 条，保留原门禁后 operational measurement 恢复 11 条、回归 1 条。有效关键点数量在 76 帧增加、107 帧减少、227 帧不变，证明扩大上下文不是单调提高可观测性。

按“生产 ROI 被画面边缘裁切”筛选出的 233 帧候选仍包含同一条 operational 回归，因此该条件不能作为安全自动 fallback。M67 不按结果挑帧、不在 baseline 缺失处偷用实验输出，也不把关键点数或 Pose 输出率称为准确率。

M66 的 149 个小 ROI 目标帧与 M67 的 410 个 margin 目标帧在每段视频内互斥。组合评测把两类实验 Pose 合回完整帧序列，再重新运行一次固定边界特征计算；不是把两份报告的恢复数相加。559 个目标帧的组合投影将特征完整实例由 2,297/2,366 提升为 2,331/2,366，门禁后 measured 由 2,291 提升为 2,320；实际恢复 30、回归 1，净增加 29。18 条 hard fail 不变，仍有 28 条非 hard-fail 特征缺口。

因此生产继续保留 0.15 margin、32px 最小 ROI 和无自动 fallback。机器入口为 `reports/measurement-recovery-m67/summary/report.json`，三段 H.264 动态 A/B 位于 `reports/measurement-recovery-m67/roi-margin-0.30/<video_id>/`。当前人工校正关键点、分视角特征误差和预注册独立视频发布测试仍缺失，grade=0、threshold=0、13 项继续保持 F2。

## 15.68 M68：用 required-joint validity 严格超集收窄实验 Pose 路由

M68 没有把 M67 的 0.30 margin 全量结果直接设为自动 fallback，而是从当前 13 项注册表的 required feature 解析出真正参与评分的 8 个身体关节：左右肩、髋、膝和踝。逐帧比较当前 0.15 ROI 与候选 0.30 ROI 时，只有候选有效关节集合严格包含当前集合、且当前任一有效评分关节都没有丢失，才允许采用候选 Pose。该选择不读取 feature value、事件结果、grade 或 threshold；未知 feature 到关节的映射必须 fail closed。

三视频 410 个 margin 候选帧中，严格超集路由只选中 44 帧并拒绝 366 帧；与 M66 的 149 个小 ROI 恢复帧合并后，实际实验路由共 193 帧。将这些 Pose 合回完整时间线并重新运行固定边界特征计算后，特征完整实例由 2,297/2,366 提升为 2,329/2,366，恢复 32、回归 0；保留原测量门禁后，operational measured 由 2,291 提升为 2,320，恢复 29、回归 0。18 条 hard fail 不变，剩余 28 条非 hard-fail 特征缺口。

该策略在当前三视频获得与 M67 无筛选组合相同的净 operational 增益，同时消除当前集合中的 1 条回归，并把 margin 路由范围从 410 帧缩到 44 帧。但“当前三视频 0 回归”不等于关键点更准确，也不是预注册独立发布验证。生产仍保持 0.15 margin、32px 最小 ROI、无自动 fallback；人工校正关键点、分视角特征误差和独立视频测试完成前不晋级。机器入口为 `reports/measurement-recovery-m68/summary/report.json`，三段动态视频位于 `reports/measurement-recovery-m68/required-joint-superset/<video_id>/`，维护说明见 `docs/MULTIVIDEO_POSE_OBSERVABILITY_ROUTER_M68.md`。

M68 最终验证：全仓 601 tests OK（16 个可选依赖跳过）；3 份逐视频实验和 1 份聚合报告均通过 Python validator、Draft 2020-12 Schema 与来源 SHA 重放；三段 H.264 视频为 44/336/66 帧，首/中/末帧全部可解码；主页检查 163 个本地引用，缺失 0，本地 HTTP 主页和视频均返回 200。

## 15.69 M69：只对 M68 残差事件运行高分辨率候选

M69 先从 M68 固定边界结果重放出 28 条非 hard-fail feature-incomplete 指标实例、13 个事件与 258 个目标事件帧，再只对这些帧运行 RTMPose-M Halpe26 384×288、0.30 ROI 候选。258 帧中 255 帧得到 Pose；沿用 required-joint 严格超集门禁后只采用 40 帧，拒绝 218 帧。候选选择不读取 feature value、事件结果、grade 或 threshold。

将 40 帧实验 Pose 合回 M68 全时间线并固定同一事件边界重算后，feature-vector complete 由 2,329/2,366 提升为 2,335/2,366，operational measured 由 2,320/2,366 提升为 2,326/2,366；两个口径均恢复 6、回归 0。非 hard-fail 特征残差由 28 降到 22，18 条 measurement hard fail 不变。

候选同时改变 RTMPose 权重/输入分辨率和裁剪上下文，当前不能把增益单独归因于任一因素。三段当前视频零回归也不是人工关键点准确率或独立发布验证；生产不自动切换模型或 profile，13 项继续为 F2，grade=0、threshold=0。机器入口为 `reports/measurement-recovery-m69/summary/report.json`，动态视频位于 `reports/measurement-recovery-m69/high-resolution-384-margin030/<video_id>/`，维护说明见 `docs/MULTIVIDEO_HIGH_RESOLUTION_RESIDUAL_RECOVERY_M69.md`。

M69 最终验证：全仓 607 tests OK（16 个可选依赖跳过）；残差审计、3 份实验报告与聚合报告通过 Python validator、Draft 2020-12 Schema 和 source replay；三段 H.264 动态视频为 43/58/33 帧，首/中/末全部可解码。主报告 175 个本地引用、94 个唯一目标，缺失 0；浏览器实测两段内嵌视频 `readyState=4`、无媒体错误，主页和三段视频 HTTP 均返回 200；`compileall` 与 `git diff --check` 通过。

## 15.70 M70：分离 Pose profile 与裁剪上下文，并以 M69 为不可回退锚点

M69 的 384×288 候选同时改变了 Pose 权重/输入尺寸和裁剪上下文，因此本轮先精确重放 M68 每个残差目标帧实际采用的裁剪来源：179 帧使用 baseline 0.15 margin/32px，28 帧使用 0.30 margin/32px，51 帧使用 0.15 margin/8px。保持这些上下文不变，只替换为 RTMPose-M Halpe26 384×288 后，258 个目标帧有 254 个 Pose 输出；required-joint 严格超集门禁选中 32 帧，固定边界 operational 只恢复 2 个实例。由此确认 M69 的 6 个恢复不能单独归因于分辨率。

直接把同上下文候选与 M69 统一 0.30/8px 候选放入“必需关节有效数最多”路由，选中 42 帧，特征向量净恢复 8、operational 净恢复 6、相对 M68 均无回归；但它丢失了 1 个 M69 已恢复 operational 实例，同时在另一事件补回 1 个，说明总计数相同不能证明逐实例保持。该策略因此明确拒绝，不能进入生产。

安全扩展从 M69 已路由帧出发，只在同上下文 384×288 候选的 required-joint 有效集合严格包含 M69 当前集合时替换。三视频额外选择 6/9/0 帧，共 15 帧；完整特征实例由 M68 的 2,329/2,366 提升为 2,338/2,366，恢复 9、回归 0；保留原事件/身份门禁后 operational measured 由 2,320 提升为 2,327，恢复 7、回归 0。它保留 M69 全部 6 个 operational 恢复，并额外恢复视频 1 的 FS09-M03 一个实例；非 hard-fail 残差由 28 降至 21，18 条 hard fail 不变。

机器入口为 `reports/measurement-recovery-m70/summary/report.json`。视频 1/2 的 M69 vs M70 动态对比位于 `reports/measurement-recovery-m70/m69-anchored-extension-v1/<video_id>/`；视频 3 没有选中扩展帧，所以不生成伪变化视频。两段 H.264 在浏览器实测 `readyState=4`、媒体错误为空。当前仍缺人工校正关键点、分视角特征误差与预注册独立视频发布验证，因此生产默认、F2、grade=0、threshold=0 均不改变。

M70 最终验证：全仓 614 tests OK（16 个可选依赖跳过）；3 份消融报告、3 份锚定报告与聚合报告通过 Python validator、PowerShell Draft 2020-12 Schema 校验和 source SHA 重放。两段视频为 H.264 57/58 帧，首/中/末解码通过；主报告 183 个本地引用、97 个唯一目标，缺失 0；浏览器实际播放验收通过。

## 15.71 M71：更大 RTMPose-L 只作为 M70 残差的严格超集扩展

M71 没有根据“模型更大”直接替换生产 Pose，而是把官方 RTMPose-L Halpe26 384×288 注册为独立候选，绑定 checkpoint SHA、字节数、官方配置和输入拓扑。它只运行 M70 已冻结的 258 个残差帧，并逐帧复用 M68 的真实裁剪上下文；254 帧产生候选 Pose。路由从 M70 frames 出发，只在 L 的 registry-required joint 有效集严格包含 M70、且不丢失任何 M70 有效必需关节时才替换，选择过程不读取 feature value、event outcome、grade 或 threshold。

三视频共选择 46 帧。固定相同候选边界重算后，特征完整实例由 M68 的 2,329/2,366、M70 的 2,338/2,366 提升到 M71 的 2,344/2,366；门禁后 operational measured 由 2,320、2,327 提升到 2,333。相对 M70 新增 6 个完整特征实例和 6 个 operational 实例，lost M70 recovery 和相对 M68 regression 均为 0；18 条 hard fail 不变，非 hard-fail operational 残差降至 15。视频 1/2 各新增 3 个 operational 实例；视频 3 虽有 60 个 L Pose 输出，却没有任何帧通过严格超集条件，因此不生成伪变化视频。

两段 M70 vs RTMPose-L H.264 动态对比位于 `reports/measurement-recovery-m71/rtmpose-l-384-context-extension-v1/<video_id>/`，分别为 63/88 帧，首/中/末解码通过。机器汇总为 `reports/measurement-recovery-m71/summary/report.json`，专项说明为 `docs/MULTIVIDEO_LARGER_POSE_MODEL_M71.md`。当前增益只证明三视频残差上的可观测性，不证明关键点位置准确、事件准确或 A～E 评分有效；生产默认、自动 fallback、F2、grade=0、threshold=0 均不改变。下一步必须使用人工校正关键点做分视角误差评测，并在未参与选择的独立视频上按预注册协议发布验证。

M71 最终验证：全仓 618 tests OK（16 个可选依赖跳过）；3 份 per-video 报告和 1 份聚合报告通过 Python validator、Draft 2020-12 Schema 和所有 source/artifact SHA 重放；两段视频为 H.264 63/88 帧且首/中/末可解码，浏览器内嵌实测 `readyState=4`、媒体错误为空。

## 15.72 M72：同拓扑缺失关键点加法融合必须逐点保持 M71

M72 对 M71 剩余 15 条非 hard-fail operational 残差做精确重放，没有继续扩大模型或放宽特征门禁。新融合器只允许在同一 Halpe26 拓扑、同一视频/帧/人物、同一非 Pose 字段下，把 M71 中无效的评分必需关节或 fine-foot 参考点用 RTMPose-L 的有效观测补齐；M71 已有效的每一个关键点必须逐字节保持，覆盖次数固定为 0。允许点集合由当前指标所需关节和左右 big toe/small toe/heel 组成，选择不读取 feature value、event outcome、grade 或 threshold。

三视频共改变 46 帧、增加 79 个有效关节观测，其中视频 1 为 25 帧/48 点、视频 2 为 21 帧/31 点、视频 3 为 0。固定同一候选事件边界、Track、特征函数和原质量门禁重算后，feature-vector complete 从 M71 的 2,344/2,366 提升至 2,346/2,366；operational measured 从 2,333 提升至 2,335。相对 M71 新增 2 个完整特征实例和 2 个 operational 实例，丢失或回归均为 0；恢复的是视频 1 的 FS01-M02 与 FS02-M02。视频 2 虽补入 31 个点却没有完成任何指标，说明“补点数量”不能作为评分恢复或准确率。

18 条 measurement hard fail 保持不变；非 hard-fail operational 残差降为 13。剩余项严格分为事件内观测覆盖不足 9 条、视频起点边界截断 2 条、必需 Pose 阶段代理未观测 2 条。它们继续保持 unavailable，禁止通过降低 `valid_fraction`、跨事件借帧、把缺失写成 0 或伪造关键阶段来清零。

只有视频 1 产生指标级恢复，因此只生成一段 76 帧、2.535091 秒的 H.264 动态对比；视频 2/3 不生成伪恢复视频。机器汇总为 `reports/measurement-recovery-m72/summary/report.json`，专项说明为 `docs/MULTIVIDEO_ADDITIVE_KEYPOINT_FUSION_M72.md`。当前融合仍是需人工真值的实验投影：候选补入的点可能位置错误，必须完成人工逐关节裁决、分视角 MAE/P95/PCK 和未参与选择视频上的预注册独立验证后才能讨论生产启用。13 项继续保持 F2，grade=0、threshold=0。

M72 最终验证：全仓 624 tests OK（16 个可选依赖跳过）；3 份逐视频报告和 1 份聚合报告通过 Python validator、Draft 2020-12 Schema、source/artifact SHA 重放；动态视频 SHA 为 `FD2D75...B151C097`，浏览器实测 `readyState=4`、媒体错误为空；主报告和模型对比文档已重建。

## 15.73 M73：WholeBody133 更多采样点不等于剩余指标可计算

M73 直接验证“更完整的全身精细采样点能否继续解决 M72 剩余缺口”。官方 RTMPose-M WholeBody133 256×192 仅在 M71/M72 已冻结的 258 个残差帧上运行，逐帧复用原裁剪上下文。跨拓扑融合只允许使用版本化一对一同名映射，将 WholeBody133 的左右肩、髋、膝、踝以及 big toe/small toe/heel 补入 M72 中无效的对应 Halpe26 点；M72 已有效坐标、Halpe26 拓扑、检测、Track、事件和非 Pose 字段全部保持。映射和选择不读取 feature value、event outcome、grade 或 threshold。

三视频得到 254/258 个 WholeBody133 候选 Pose，49 帧共增加 101 个有效同名点：视频 1 为 39 帧/85 点，视频 2 为 10 帧/16 点，视频 3 为 0。固定相同候选边界重算后，新增 feature-vector complete 为 0，新增 operational measured 也为 0；M72 的 2,346/2,366 feature complete、2,335/2,366 operational measured、18 条 hard fail 和 13 条 non-hard-fail 残差全部保持，回归与 M72 恢复丢失为 0。

因此 M73 是一个重要负结果：WholeBody133 确实补出了更多细分点，但没有解决当前 9 条事件内连续观测覆盖不足、2 条视频起点边界截断和 2 条 FS09 必需阶段代理未观测。后续优化重点必须转向事件内 timestamp 连续覆盖、边界审计和阶段证据，不能继续把“模型点更多”当作评分能力，也不能通过降低 valid fraction、跨事件借帧、填 0 或虚构 phase 清零。

视频 1/2 分别生成 112 帧/3.735924 秒和 130 帧/4.333333 秒 H.264 动态对比，即使结果为 0 个新增指标也保留真实视觉证据；视频 3 没有点级变化，不生成伪视频。机器汇总为 `reports/measurement-recovery-m73/summary/report.json`，专项说明为 `docs/MULTIVIDEO_WHOLEBODY_MAPPED_FUSION_M73.md`。当前仍缺跨模型人工逐点真值和独立视频验证，WholeBody133 映射融合不进入生产、标定或成熟度晋级，13 项保持 F2，grade=0、threshold=0。

M73 最终验证：全仓 630 tests OK（16 个可选依赖跳过）；3 份逐视频报告和 1 份汇总通过 Python validator、Draft 2020-12 Schema 和所有 source/artifact SHA 重放；两段视频在浏览器实测 `readyState=4`、1920×720、媒体错误为空；主页本地引用缺失为 0。

## 15.74 M74：事件内双侧有界短缺口只读反事实

M74 没有继续更换 Pose 模型，也没有放宽 0.5 的特征有效率门禁。新增纯函数只处理同一事件内部、被两个真实有效 Pose 观测夹住、两端 timestamp 跨度不超过现有 smoothing 合同 160 ms 的缺口；用真实 timestamp 做线性坐标插值，有效置信度取两端观测的较小值。视频首尾、事件首尾、跨事件、长缺口、补 0 和 phase 生成均被禁止，且输出明确标记为 counterfactual，不是模型观测。

在 M73 冻结的 13 个 non-hard-fail 残余实例上，逐指标重放共涉及 226 个插值关节观测；去除同一 event×joint×frame 被多个指标重复引用后为 142 个唯一候选点。157 个可插值运行的双端跨度为 66～134 ms。固定事件边界和当前特征公式重算产生 20 个 invalid→valid 特征转换，使 7/13 个残余特征向量在反事实中完整；其余 6/13 保持不完整，其中包含 2 个 FS09 必需阶段代理未观测。该结果只说明短缺口是一个可量化的测量敏感性来源，不证明 142 个插值坐标正确。

因此生产恢复数固定为 0：没有生成可被 Worker 或评分入口加载的 Pose JSONL，原 13 个实例继续 `unavailable`，13 项成熟度仍为 F2，grade=0、threshold=0。下一步应把 142 个唯一候选点转换为盲化人工关键点任务，对插值坐标计算 MAE/P95/Bias 和分视角结果；只有误差预算小于外部等级间差异且独立测试通过，才考虑版本化推理级缺口策略。机器逐点审计为 `reports/measurement-recovery-m74/event-bounded-gap-audit-v1/report.json`，算法与契约见 `src/rallymate_evaluation/event_bounded_pose_gaps.py`。

M74 最终验证：全仓 636 tests OK（16 个可选依赖跳过）；6 个 M74 专项测试通过；机器报告通过 Python validator、source SHA replay 与 Draft 2020-12 Schema；主报告 206 个本地引用、106 个唯一目标、缺失 0；浏览器内 M74 区块可见，既有 23 段动态视频全部 `readyState=4` 且媒体错误为空；`py_compile` 与 `git diff --check` 通过。

## 15.75 M75：M74 插值候选进入双人盲标与独立裁决，而不是生产 Pose

M75 将 M74 的 142 个唯一 `event×joint×frame` 候选固定为人工关键点任务，覆盖两段真实视频的 54 个源帧、13 种肩/髋/膝/踝及 fine-foot 关节。任务只来自 M74 的可插值点，不扩展到视频起点缺口或 FS09 缺阶段实例；因此它评测的是“事件内短缺口插值坐标误差”，不是整个 Pose 模型、事件检测器或 13 项指标的总体准确率。

页面只加载无骨架原视频、任务关节名和用于查看的主球员裁剪框。142 个插值坐标、两侧证据帧和候选置信度单独保存在密封 JSONL 中，不进入浏览器 bootstrap，也不预填人工 CSV。两个当前 Pose 为空的帧使用 160 ms 内前后主球员框的并集作为标注视图，并显式声明该框不是身份真值或关键点真值。

每个点必须有两名不同标注者的独立记录，再由未参与两份标注的 reviewer 接受裁决。编译器逐任务验证身份独立性、可见点归一化坐标、不可见原因、source annotation IDs、任务与密封预测 SHA；只有 142/142 完整裁决后才计算插值 MAE、P95、二维 Bias、有效率、按关节、按视角和按事件族结果。外部预注册接受协议仍单独必需，评测器不会自行生成误差门槛。

当前真实包为 0 条 annotation、0 条 accepted adjudication，状态 `annotation_required`；空白误差报告的所有 metrics 均为 null。M74 反事实 7/13 不进入 Worker、生产 Pose、标定数据集或 F3/F4 晋级。入口为 `data/annotations/event-bounded-pose-gap-truth-m75-v1/review.html`，机器空白报告为 `reports/measurement-recovery-m75/event-gap-keypoint-error-empty.json`，说明见 `docs/EVENT_GAP_KEYPOINT_TRUTH_M75.md`。

M75 契约验证使用显式 synthetic-only 坐标证明 142 点完整链路能得到 MAE/P95/Bias=0 和 valid rate=1；该合成结果不写入真实包，也不是模型效果。篡改密封预测、同步重算 task/prediction SHA、reviewer 与 annotator 重合、重复输出目录均 fail closed；编译器会从绑定 M74 报告重放任务和密封预测，不能只信任自报哈希。专项 6/6、两个 JS `node --check`、三个真实 JSON 的 Draft 2020-12 Schema 均通过；全仓 642 tests OK（16 skipped）。主报告 212 个本地引用/109 个唯一目标缺失 0，M75 工作台 2 个视频引用缺失 0；浏览器实测两段视频切换后均 `readyState=4`、媒体错误为空，142 个任务全部加载且 bootstrap 无密封预测字段。

## 15.76 M76：从关键点真值进入可评测特征误差，不把反事实当准确率

M76 新增真值条件特征重放层：对 M74 中真正含有界插值点的 11 个指标实例，固定候选事件边界、Track 和非缺口 Pose，重算 47 条 required-feature 记录（25 个唯一特征）。可见人工点只替换坐标，候选置信度冻结以隔离坐标/可见性误差；不可见点恢复为 NaN，不以 0 表示缺失。`launch_direction_deg` 使用环形差，code 特征只计精确一致率。

2 个 FS09-M05 残差缺的是 required phase proxy，没有可插值点，因此不进入 M76 并继续 unavailable。M76 输出不是全 Pose 特征误差或 Event F1，不能直接改变生产路由、评分门禁或 F2→F3。当前 M75 人工裁决为 0，所以真实 M76 报告为 `annotation_required`，所有误差指标为 null，生产插值、grade、threshold 和 F3/F4 晋级均关闭。机器产物为 `reports/measurement-recovery-m76/event-gap-feature-error-empty.json`，维护说明为 `docs/EVENT_GAP_FEATURE_TRUTH_M76.md`。

M76 验收包含：真实空包来源重放、synthetic-only 142 点完整裁决下 47 条特征零差对照、不可见点不填 0、角度环形差和报告篡改来源重放。专项 5/5，真实 M76 JSON 通过 Draft 2020-12 Schema，全仓 647 tests OK（16 skipped）；`compileall`、JS 语法与 `git diff --check` 通过。重建后主报告有 214 个本地引用/110 个唯一目标，缺失 0；应用内浏览器已实测 M76 标题、11/47/25 范围和 null 安全结论正确渲染。

## 15.77 M77：FS09 阶段缺口必须由盲化视频真值回答

M77 对 M76 排除的 2 个 FS09-M05 实例进行精确原因审计。两者事件内左右髋/踝观测均为 100%，唯一 invalid required feature 为 `hip_deceleration_to_double_support_proxy_ms`；当前公式没找到双踝同时低运动区间的起点，而不是 Pose 空间采样点不足。因此继续插值、增加拓扑或将缺失写 0 都不能解决该问题。

新工作包只给标注者播放无骨架、无音频、无候选叠加的 padded 审阅视频，bootstrap 不含候选 event_id/start/end/阶段。为避免 479 秒源视频在浏览器中随机 seek 失败，两个窗口已分别物化为 3.875 秒和 3.542 秒的 H.264/faststart 短片；manifest 同时绑定源视频、M74 报告、ffmpeg、短片 SHA、首/中/末帧解码结果和 `source_time_offset_ms`。界面使用短片播放时间，但所有“取当前视频时间”均换算回原视频绝对 `timestamp_ms`。每个任务需双人独立标注 FS09 存在性、边界、峰速、减速峰、重新稳定与稳定控制起点，再由独立 reviewer 裁决。评测器输出 Event IoU/Boundary MAE、phase MAE/Bias，以及同一模型 Pose 在人工 event interval 下重算六项 FS09-M05 特征的边界敏感性。人工稳定控制只是视觉状态，不是双支撑、触地、足压或受力真值，也不会被强行写入低踝运动 Pose 代理。

当前真实包为 0 annotation/0 adjudication，所以 Event、phase 和 feature metrics 均为 null，运行时事件/特征不修改。synthetic-only 合同证明：两个真值事件与候选相同时 IoU=1、边界/阶段误差为 0，但目标低踝运动特征仍为 0/2 valid，证明代码没有用人工 phase 冒充 Pose 代理。工作台为 `data/annotations/fs09-phase-truth-m77-v1/review.html`，空白报告为 `reports/measurement-recovery-m77/fs09-phase-truth-empty.json`，说明见 `docs/FS09_PHASE_TRUTH_M77.md`。

M77 最终验证：专项 8/8；全仓 655 tests OK（16 skipped）；4 份真实机器文档通过 Draft 2020-12 Schema、Python validator 与来源重放；两段 H.264 均完成首/中/末帧解码，且重算 manifest SHA 后伪造 probe 字段会由实际媒体重解码拒绝。应用内浏览器实测两段视频 `readyState=4`、媒体错误为空，短片 0 ms 分别映射为原视频 331458 ms 与 450083 ms，页面未暴露候选 event_id 或候选时间。主报告 220 个本地引用/113 个唯一目标，缺失 0；`compileall`、两个 JS `node --check` 与 `git diff --check` 通过。

## 15.78 M78：类型化阻断必须有单一机器真源，不能把所有失败归为身份问题

M78 审计确认当前评分运行时已经能分别输出关键点跳变、左右点交换、目标方向、主球员身份、事件边界/阶段、Pose 覆盖和启动脚侧别原因；真正的维护风险是相同映射分别存在于评分、readiness、blocker audit 和人工工作清单中。新增 `scoring-blocker-taxonomy-v1.0.0`，为每个 flag 统一记录稳定 reason code、人工真值要求、blocker group 和反馈；未知 flag 进入外部人工复核，不猜成身份问题。

当前三视频 M65 机器真源共 2,366 个指标实例。活动 flag 出现次数为 jump 1,152、左右交换 488、目标方向 182、source Track 切换 125、启动脚侧别歧义 84、Pose 运动学覆盖 71、landing 右截断 45、主 Pose 覆盖 36、第一步低样本 8、第一步右截断 6；同一实例可重叠，禁止相加当作唯一实例数。评分证据阻断恢复优先级以 jump 1,025、左右交换 482、启动脚侧别 80、source Track 78 为首；它只路由人工证据，不是诊断准确率。

用当前代码复用同一 frames/primary timeline 无 GPU 重放三段视频，546 个事件、全部非 confidence 特征/指标/评分载荷以及 2,366 条 score 的 status/grade/reason/feedback/quality gate 均与 M63 基线一致。仅 258 个嵌套 confidence 叶值在序列化末位有差异，最大绝对差 0.000001；该重放容差不是评分阈值。最终状态仍为 834 calibration_required / 1,532 unavailable、grade=0、threshold=0；measurement/scoring gate、F2 和人工真值状态均未改变。机器审计为 `reports/measurement-recovery-m78/blocker-taxonomy-audit.json`，说明见 `docs/SCORING_BLOCKER_TAXONOMY.md`。

M78 最终验证为全仓 663 tests OK（16 skipped）；三套重放 bundle 均通过跨 artifact validator，分别包含 81/102/363 个事件与 351/442/1,573 条指标/评分记录；taxonomy registry 与 M78 audit 均通过 Draft 2020-12 Schema。重建主报告后有 222 个本地引用/114 个唯一目标，缺失 0。

## 15.79 M79：高频 Pose 诊断真值必须保留两份独立原始记录，并使用可播放的动态视频

M65 的单页复核队列虽然能够定位候选，却把两个 annotator ID 与 reviewer 放在同一条决定记录中，没有保存两份真正独立的原始观察，也要求在最长约 8 分钟 HEVC 视频中反复随机 seek。M79 将 score-relevant jump/swap 候选从 M78/M65 来源重新投影并冻结为 1,371 个任务：jump 1,017、swap 354，覆盖 1,336 个去重指标实例。重叠窗口合并后得到 103 个动态片段、9,636 帧；每个片段单独编码为 H.264，切片不再依赖长视频 Range seek。

第一阶段 `review.html` 的 bootstrap 只含视频、片段帧映射和诊断类型，不含 `pdr-*` task ID、候选帧、候选关节、受影响指标或模型坐标。两名标注者分别锁定不同 ID，各自导出 coverage 与 positive CSV。第二阶段 `adjudicate.html` 是隔离页面，只有在导入两人的原始文件后才可由第三名 reviewer 输出裁决；reviewer 与任一 annotator 相同、coverage 不足两份、`confirmed_true` 没有 raw positive 或引用 ID 不精确时全部拒绝。

来源校验不只比较自声明 SHA。验证器会从绑定的三份 M65 queue 和 M78 audit 重新构造 clip plan 与密封候选，再与包内 1,371 项逐项比较；因此同步修改候选并重算文件/canonical hash 仍会 fail closed。103 个媒体文件逐个重算 SHA、codec、帧数和首/中/末解码。`scripts/range_http_server.py` 为本地页面提供 HTTP 206 byte ranges，但必须显式列出 `--allow` 文件/目录；`serve_pose_scoring_ab.ps1` 只服务 `reports/pose-scoring-ab/`，不再兼作人工真值包服务器。逐帧按钮和长报告视频 seek 不依赖不支持 Range 的 `python -m http.server`，而盲标/裁决角色隔离必须使用各自独立的最小公开包。

当前真实包 `data/annotations/pose-diagnostic-clip-truth-m79-v1.1/` 为 annotation_required：coverage、positive、adjudication 均为 0，precision/recall/F1 全部 null。即使全部候选裁决完成，也只允许输出 candidate precision；候选窗口没有完整时间线负例覆盖，recall/F1 必须保持 null。M79 不自动修改质量门禁、运行候选、grade、threshold 或 F3/F4。使用说明见 `docs/POSE_DIAGNOSTIC_CLIP_TRUTH_M79.md`。

## 15.80 M80：盲标与模型证据必须分阶段解封，第三方裁决必须看得到实际点轨迹

M79 正确分离了两名独立 annotator 和第三方 reviewer，但第三方页面只显示候选帧与关节名称，没有展示模型坐标。jump/swap 是模型输出诊断；reviewer 如果只看原始视频，无法直观看出点跳到了哪里或左右点如何交换。M80 保留第一阶段完全盲化，在第二阶段增加 adjudicator-only 动态 Pose overlay。

每个 103 个片段都新增一份版本化 pose evidence sidecar，共覆盖原 9,636 帧。sidecar 从绑定的 `frames.jsonl` 与 `primary-player.jsonl` 选择同一源帧、同一 source Track 的 Halpe26 Pose；浏览器先校验文件 SHA-256，再按视频时间显示完整骨架、候选目标 joint/pair、前后各 4 帧轨迹、点置信度、源帧和 Track。Python validator 会从两个源文件逐帧 exact replay，因此同步改坐标和自声明 hash 仍被拒绝。

第一阶段 bootstrap 仍不含 pose evidence URL、候选 ID、候选帧或候选关节。模型叠加只在第三方页面解封，并明确标记为待评测模型输出而非真值。候选集合、视频、CSV 合同和 precision-only 评测均不变；当前 0/0/0 人工记录继续得到 `annotation_required` 和 null metrics，不修改任何运行 gate、grade、threshold 或 F2/F3/F4。

最终不可变包为 `data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2/`，说明见 `docs/POSE_DIAGNOSTIC_CLIP_TRUTH_M80.md`。103/103 sidecar 通过 Draft 2020-12 Schema；定向 7/7 通过。应用内浏览器实测两个页面视频均 `readyState=4`、无媒体错误，裁决 canvas 为 320×568 且显示 Halpe26 源帧/Track；盲标页候选与 pose evidence 泄漏均为 false。

最终全仓 `scripts/run_tests.ps1` 为 670 项通过、16 项可选依赖跳过；主报告 225 个本地引用（117 个唯一资源）缺失为 0，103/103 M80 媒体文件与 M79 片段 SHA-256 完全一致。

## 15.81 M81：让原视频、骨架、事件、特征和评分阻断在同一时间轴上可观察

既有主报告包含大量视频和机器数字，但用户仍需要在动作发生的那一刻直观看到：系统选中了哪个人、骨架点在哪里、当前属于哪个事件、某个指标依赖哪些关节、特征到底算出了什么，以及为什么结果仍是 unavailable 或 calibration_required。M81 新增 `reports/scoring-visual-observer-m81/index.html`，把三段当前 M59 真实评分 bundle 变成同步播放器。

观察器覆盖 3 段、15,868 帧、546 个候选事件和 2,366 条指标实例。视频拖动会同步 Halpe26 骨架、三事件时间轴、阶段、Track/Pose 诊断、13 项指标、required joint 高亮、特征 value/unit/confidence/valid/reason/source_frames 与质量门禁。三视频汇总为 feature measured/unavailable=2,291/75，scoring calibration_required/unavailable=834/1,532，grade=0、threshold=0。

每段浏览器数据都绑定原视频、frames、primary timeline、events、indicator-features、scores、summary 与 registry SHA。浏览器校验实际 JSON 文件 SHA-256，机器 validator 校验 canonical content、连续帧、指标全集与状态不变量。该页面只改善可解释观察，不把模型 Pose 或规则事件冒充真值，也不定义总分。维护说明见 `docs/DYNAMIC_SCORING_OBSERVER_M81.md`。

M81 完成后全仓 `scripts/run_tests.ps1` 共运行 674 项并全部通过，16 项可选 `jsonschema` 检查因本机依赖未安装而跳过；主报告 226 个本地引用（118 个唯一资源）与观察器 2 个静态引用均无缺失。三段视频在浏览器中的播放、拖动、事件跳转和指标切换均已实测，媒体状态为 `readyState=4`、`error=null`。

## 16. 后续确认点

工程里程碑已完成；下一次晋级不是继续增加规则，而是导入人工事件、人工关键点和多教练标签。任何真实数据目标、事件匹配容忍、stability envelope、A～E 阈值或 F3/F4 晋级门槛，都必须在查看独立测试结果前单独确认和版本化，不能由实现代码自行猜测。
