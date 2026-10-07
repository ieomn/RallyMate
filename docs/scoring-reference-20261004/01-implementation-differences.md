# 实现差异与前端解释要求

本清单以提取时的本地实现快照为准；源内异常另见主JSON的 conflicts。状态“一致”仅代表所述层面一致，不自动代表已能正式评分。

## CORE-01 0.40/0.25/0.20/0.15维度权重、GS/FS=0.70/0.30模块权重没有（不一致）

原文定位：全部七份Word；298卡A级至E级行均为定性描述

实现字段：scoring-demo-web/app/scoring/engine.ts:104-111 DIMENSION_WEIGHTS/MODULE_WEIGHTS

0.40/0.25/0.20/0.15维度权重、GS/FS=0.70/0.30模块权重没有Word依据。

建议：只标记演示规则；正式启用须另建经授权、可追溯和已标定的权重契约。

## CORE-02 90/80/70/60分界没有源文档依据，定性等级不等于该分段。（不一致）

原文定位：CARD-FS/CARD-GS 全部A-E等级描述

实现字段：scoring-demo-web/app/scoring/engine.ts:127-133 gradeFor

90/80/70/60分界没有源文档依据，定性等级不等于该分段。

建议：不得称Word原文阈值；真实分级展示须引用标定资产版本和原始特征。

## CORE-03 演示维度分由scenario.baseScore、文本命中加成与ID确定性哈希扰动生成，并（不一致）

原文定位：CARD-FS/CARD-GS 计算方式行

实现字段：scoring-demo-web/app/scoring/engine.ts:118-125,171-185 dimensionScores

演示维度分由scenario.baseScore、文本命中加成与ID确定性哈希扰动生成，并非视频实测；也不是从100分按缺陷扣分。

建议：演示页明示人工演示数据，不应将其分差解释为实际动作扣分。

## CORE-04 指标按事件等权平均；模块按事件等权平均；两模块按70/30组合。GS事件指标数不同，跨事件（需标定）

原文定位：298卡没有事件、阶段或模块聚合公式

实现字段：scoring-demo-web/app/scoring/engine.ts:249-320 aggregate/aggregateDomain/buildScoreReport

指标按事件等权平均；模块按事件等权平均；两模块按70/30组合。GS事件指标数不同，跨事件隐含单指标权重不同；没有阶段显式权重。

建议：文档与UI须展示实际分母及跳过缺测项策略；正式聚合前补阶段/事件/模块权重与最少有效样本要求。

## CORE-05 真实模式固定score=null、grade=null，ready仍要求完成事件切分与特征（一致）

原文定位：CARD-GS:word/document.xml:P0006；全部卡不可评价字段

实现字段：scoring-demo-web/app/scoring/engine.ts:193-221 evaluateCard real branch

真实模式固定score=null、grade=null，ready仍要求完成事件切分与特征标定。

建议：保留门禁；ready文案应写证据预检可用，不能写技术合格或A级。

## CORE-06 0.85/0.15证据组合、必需依赖0.65、ready 0.72、partial 0.4（不一致）

原文定位：CARD-FS 34卡及CARD-GS248卡不可评价行的有效帧<70%

实现字段：scoring-demo-web/app/scoring/engine.ts:164-166,195-198 evidenceFor/evaluateCard

0.85/0.15证据组合、必需依赖0.65、ready 0.72、partial 0.40是工程启发式；汇总coverage不是指标所需关键点在事件内的70%有效帧。

建议：分别命名与记录阈值来源；不得宣称该ready门槛已满足逐指标原文门槛。

## CORE-07 实际资源为src/rallymate_scoring/data/technique_met（一致）

原文定位：五份视觉定义只给动作参考，不给100分评分系统

实现字段：src/rallymate_scoring/technique_registry.py:20,111-115；data/technique_metrics.json semantics

实际资源为src/rallymate_scoring/data/technique_metrics.json；不存在根data/technique_registry目录。两端目录保留formal_coach_score=false、缺测不插补，五源SHA均匹配。

建议：用准确资源路径和SHA作来源，保留正式评分关闭。

## CORE-08 证据就绪度=round(100*(0.7*必需覆盖率最小值+0.3*增强覆盖率平均值))，（需标定）

原文定位：五份视觉定义没有证据权重或就绪分公式

实现字段：src/rallymate_scoring/technique_assessment.py:756-758,784-786,806,848-852

证据就绪度=round(100*(0.7*必需覆盖率最小值+0.3*增强覆盖率平均值))，不是技术分；未观测项输出0仅为就绪度占位。

建议：用户层对未观测项显示未观测/不适用；禁止以0或100减就绪度当技术分/扣分。注明覆盖率作用域和代理来源。

## CORE-09 证据无效返回unavailable；缺标定返回calibration_required；生（一致）

原文定位：各卡不可评价条件及定性A-E

实现字段：src/rallymate_scoring/scoring.py:206-283,286-472

证据无效返回unavailable；缺标定返回calibration_required；生产评级要求F4、独立测试、可信晋级、注册表和运行姿态/事件/质量版本绑定。

建议：前端沿用后端status/reason_codes，不依据sourceStatus自行评级；保留测量可用但正式评分被阻断的状态。

## CORE-10 后端支持4个递增切点的单变量A-E或有序回归；实际阈值必须来自coach_ground_t（需标定）

原文定位：卡片未给数值切点

实现字段：src/rallymate_scoring/scoring.py:481-535,538-621；calibration.py:287-332

后端支持4个递增切点的单变量A-E或有序回归；实际阈值必须来自coach_ground_truth_calibration，且单位/特征版本匹配。没有通用100分或扣分返回值。

建议：不能把后端A-E反解为90/80等分值；未取得有效资产时正式分/等级保持null。

## CORE-11 旧版6项、新版13项全部F2，覆盖FS01/FS02/FS09；没有GS、发球、接发、网前（未实现）

原文定位：298张卡整体

实现字段：metric-feasibility.json /indicators；metric-feasibility-pose-wave-v2.json /indicators；indicator-scoring-requirements-pose-wave-v1.json /indicators

旧版6项、新版13项全部F2，覆盖FS01/FS02/FS09；没有GS、发球、接发、网前正式评级实现。13项具备可测代理不表示已通过教练标定。

建议：按具体本次运行绑定的注册表及事件证据展示，不能宣称298项或24技术都能正式评分。

## CORE-12 measurement plans仍声明pose-wave-2026-08-21.8，而当（不一致）

原文定位：来源卡与指标语义需版本绑定

实现字段：metric-measurement-plans.json /source_feasibility_registry_version,/plans/*/versions；metric-feasibility-pose-wave-v2.json /registry_version

measurement plans仍声明pose-wave-2026-08-21.8，而当前v2与requirements为2026-10-01.2。计划13个F2数量一致不能证明特征/语义完全一致。

建议：重新生成并校验逐指标特征、阶段、语义键和SHA绑定；交接JSON并列保留现状，禁止静默更新。

## CORE-13 特征有效率0.50与事件运动学覆盖0.75是不同工程门禁；部分稀疏数据可保留F2测量，正式（需标定）

原文定位：70%原文逐卡门槛

实现字段：src/rallymate_features/event_features.py:45,750,813,910；src/rallymate_events/rules.py:31；src/rallymate_scoring/quality_policy.py:25-47

特征有效率0.50与事件运动学覆盖0.75是不同工程门禁；部分稀疏数据可保留F2测量，正式评级另阻断。不能认定0.50自动满足原文0.70。

建议：正式启用前逐指标落实有效帧分母、关节集、遮挡/身份处理和0.70证据规则；保留工程测量门槛与原文评价门槛区别。

## CORE-14 正coverage只给contact_window_proxy或impact_window（一致）

原文定位：CARD-GS:word/document.xml:P0006；VIS文档代理限制

实现字段：src/rallymate_scoring/technique_assessment.py:46-74 CONTACT_POLICY

正coverage只给contact_window_proxy或impact_window_only，范围仍是run级，既不证明真实触球也不是命中率。

建议：面向用户写候选触球时窗/需要复核；未关联事件与球拍的观测不可变成命中数或技术分。

## FS-SOURCE-VERSION 文件名第二版与正文第一版冲突（不一致）

原文定位：["CARD-FS:word/document.xml:P0001"]

实现字段：[]

文件名 FS步伐事件评分指标卡_第二版.docx；正文标题写第一版。不能靠文件名推断已批准的版本优先级。

建议：以本次 SHA256 固定原件；请规则维护者确认正式版本。

## FS-SOURCE-START FS01起点同文档存在两种表述（不一致）

原文定位：["CARD-FS:word/document.xml:P0011", "CARD-FS:word/document.xml:P0062"]

实现字段：["src/rallymate_scoring/data/metric_cards.json#/cards[id=FS01-M02]/startAction"]

总表为重心向下，微蹲；事件详细标题为重心预加载开始。当前 cards 使用后者。大体同义，但没有精确边界算法。

建议：保留两处；详细卡用于可追溯描述，总表保留别名，边界需人工定义/标注。

## FS-NUMERIC-GRADE 原文没有百分制、数字等级边界、权重或扣分公式（需标定）

原文定位：["CARD-FS:word/document.xml:P0082", "CARD-FS:word/document.xml:P0090"]

实现字段：["scoring-demo-web/app/scoring/engine.ts:104", "scoring-demo-web/app/scoring/engine.ts:127", "scoring-demo-web/app/scoring/engine.ts:171", "scoring-demo-web/app/scoring/engine.ts:249"]

前端演示使用40/25/20/15四维权重，GS/FS 70/30，90/80/70/60分级，指标/事件等权聚合，scenario.baseScore 加确定性 hash 扰动；均不是 FS 原文规则。real 分支已置 score/grade 为 null。

建议：仅作为清楚标注的演示；正式输出缺少教练标定时保持 null。不能把 100-score 解释成逐项扣分。

## FS-E-GRADE-NOT-MISSING E级未观察到行为不能代替不可评价（一致）

原文定位：["CARD-FS:word/document.xml:P0090", "CARD-FS:word/document.xml:P0092"]

实现字段：["src/rallymate_scoring/scoring.py:210", "src/rallymate_scoring/scoring.py:235", "src/rallymate_scoring/scoring.py:271", "metric-measurement-plans.json#/policy"]

源文分别列 E级与不可评价；Python 在质量失败/特征无效时 unavailable/grade=null，缺标定 calibration_required/grade=null；plans 明确 null_not_zero。

建议：前端保留独立状态；只有已满足观测条件且完成标定后才能给 E。

## FS-COVERAGE-GATE 源70%指标关节帧门槛不能被统一证据分替代（不一致）

原文定位：["CARD-FS:word/document.xml:P0092", "CARD-FS:word/document.xml:P0127"]

实现字段：["scoring-demo-web/app/scoring/engine.ts:143", "scoring-demo-web/app/scoring/engine.ts:193", "src/rallymate_features/event_features.py:45", "src/rallymate_scoring/quality_policy.py:34"]

34项明确低于70%不可评价，部位各异。engine 用依赖覆盖率<0.65及混合evidence>=0.72；特征库 MIN_VALID_FRACTION=0.50 是F2测量可用门槛。并非同一统计口径；已读评分路径未见逐指标执行源关节70%规则。

建议：逐指标记录时间窗、所需关节和有效帧分母；F2测量允许保留但不得称满足源评分门槛。正式评分前执行原文门槛，并核定70%边界与有效帧定义。

## FS-PLANS-STALE 测量计划的完成状态/版本与子条款不同步（不一致）

原文定位：[]

实现字段：["metric-measurement-plans.json#/plans", "metric-feasibility-pose-wave-v2.json#/registry_version"]

13项 plans.runtime_status=implemented_f2_calibration_required，但内部若干条款仍写 feature_implementation_required，events 模型版本仍 pose-motion-bout-v0.1.0，plan引用feasibility pose-wave-2026-08-21.8。旧feasibility只有6项，v2/requirements为13项。

建议：建立单一生成源和版本同步检查；逐条款确认覆盖，不把F2标签解读为已完整实现原文。

## FS-OPTIONAL-DEPS 可选依赖在计划层缺少降级分支（不一致）

原文定位：["CARD-FS:word/document.xml:P0080", "CARD-FS:word/document.xml:P1713", "CARD-FS:word/document.xml:P1818"]

实现字段：["metric-measurement-plans.json#/plans[indicator_id=FS01-M01]", "metric-measurement-plans.json#/plans[indicator_id=FS10-M02]", "metric-measurement-plans.json#/plans[indicator_id=FS10-M05]"]

FS01-M01无球时允许只评价头部稳定；FS10-M02场地标定建议；FS10-M05场地信息可选。plans 只给 dependencies 集合与单一 dependency_implementation_required，不能表达降级后的可测范围。engine有文本可选识别但覆盖率不是测量。

建议：显式区分必需/增强/替代证据和 full/partial scope，不以可选球/场地缺测扣分。

## FS01-M02-DELTA 预加载源变化量与当前统计量不同（不一致）

原文定位：["CARD-FS:word/document.xml:P0115"]

实现字段：["indicator-scoring-requirements-pose-wave-v1.json#/indicators[indicator_id=FS01-M02]/required_feature_names", "src/rallymate_features/event_features.py:79"]

源要求髋中心高度下降量、双膝夹角变化和身体中心速度连续性；当前必需特征为髋高median、膝屈曲peak、站宽、支撑比，不等于下降量/变化量/连续性完整实现。

建议：补明前后窗口并计算差值/连续性；保留现有测量为代理，不给原文完整完成结论。

## FS01-M04-SCALE 踝距归一化分母与原文不同（不一致）

原文定位：["CARD-FS:word/document.xml:P0185"]

实现字段：["indicator-scoring-requirements-pose-wave-v1.json#/indicators[indicator_id=FS01-M04]/required_feature_names", "src/rallymate_features/fs01_fs02_features.py:113"]

原文为落地后踝距/髋宽比例；当前 post_slowdown_stance_width_body 为脚参考点间距/body scale，且落地只是脚减速代理。

建议：增加 ankle_distance/hip_width 的独立特征或经批准变更规则；明确脚减速不是真实触地。

## FS01-M05-CROSS-EVENT FS02启动关联与时间间隔未在必需契约中完整表达（未实现）

原文定位：["CARD-FS:word/document.xml:P0220", "CARD-FS:word/document.xml:P0232"]

实现字段：["indicator-scoring-requirements-pose-wave-v1.json#/indicators[indicator_id=FS01-M05]", "metric-feasibility-pose-wave-v2.json#/indicators[indicator_id=FS01-M05]"]

源规定FS02启动未识别不可评价，并要计算到FS02的间隔。当前契约为FS01.redistribution/FS01.initiation及4个姿态特征；没有要求独立FS02事件ID关联或跨事件间隔特征。

建议：添加同球员FS02关联及interval；未关联时仅测重心/支撑，完整指标 unavailable。

## FS02-M02-TARGET 来球方向与目标方向的语义需明确（不一致）

原文定位：["CARD-FS:word/document.xml:P0283", "CARD-FS:word/document.xml:P0285", "CARD-FS:word/document.xml:P0289", "CARD-FS:word/document.xml:P1843"]

实现字段：["metric-feasibility-pose-wave-v2.json#/indicators[indicator_id=FS02-M02]", "src/rallymate_scoring/scoring_context.py:15"]

原卡名称写重心向来球方向转换，而技术定义写目标方向；feasibility沿用目标方向并加入外部target_direction与alignment。受控训练目标可用于该扩展，但不等于原视频观测的来球方向。原卡requiredPoints只有pose，与附录完整方向评分条件亦需协调。

建议：保留原名及测量子范围；将外部目标来源/坐标系独立展示；规则维护者需明确目标是否必须等于来球方向，完整一致性须满足相应外部证据。

## FS09-M05-CROSS-EVENT 稳定控制的后续事件连续性未完整接入（未实现）

原文定位：["CARD-FS:word/document.xml:P1644"]

实现字段：["indicator-scoring-requirements-pose-wave-v1.json#/indicators[indicator_id=FS09-M05]", "metric-feasibility-pose-wave-v2.json#/indicators[indicator_id=FS09-M05]"]

必需特征/语义含稳定时长、双支撑代理、减速等，没有FS10/FS02关联要求；FS10在plans未实现。不能据内部减速/稳定推断已连续进入下一事件。

建议：增加后续同球员FS10或FS02关联，否则输出可测子项而非整项完成。

## FS-PROXY-LIMITS 视觉中心、离地/落地、发力均为代理（一致）

原文定位：["CARD-FS:word/document.xml:P1842", "CARD-FS:word/document.xml:P0340", "CARD-FS:word/document.xml:P0162"]

实现字段：["src/rallymate_features/fs01_fs02_features.py:59", "src/rallymate_scoring/quality_policy.py:80"]

特征定义明确proxy/not contact/not force；源同样禁止把髋中心当真实生物力学重心、踝速度当真实足底接触。

建议：文案统一使用视觉人体中心、脚部抬升/减速代理；不能输出真实发力牛顿值、真实注视或真实承重。

## GS-IMPL-01 248卡的14个平面原文字段及A—E共19个值与原文逐字相同；两份注册表字节相同。（一致）

原文定位：[{"source_id": "CARD-GS", "tables": "T002..T249"}]

实现字段：[{"file": "src/rallymate_scoring/data/metric_cards.json", "pointer": "/cards"}, {"file": "scoring-demo-web/app/data/metric-cards.json", "pointer": "/cards"}]

248卡的14个平面原文字段及A—E共19个值与原文逐字相同；两份注册表字节相同。

建议：在不改变原值的基础上增加SHA256、段落/表格定位及源异常标记。

## GS-IMPL-02 全部248项 event_localization.status=not_implemen（未实现）

原文定位：[{"source_id": "CARD-GS", "stages": 50}]

实现字段：[{"file": "metric-measurement-plans.json", "pointer": "/plans"}]

全部248项 event_localization.status=not_implemented；runtime_status为162项dependency_implementation_required及86项event_implementation_required。

建议：逐阶段补事件切分、证据与真实值；不能把全片检测覆盖率当事件已发生。

## GS-IMPL-03 这三份注册表分别只含6、13、13项FS指标，均无GS。（未实现）

原文定位：[{"source_id": "CARD-GS", "indicators": 248}]

实现字段：[{"file": "metric-feasibility.json", "pointer": "/indicators"}, {"file": "metric-feasibility-pose-wave-v2.json", "pointer": "/indicators"}, {"file": "indicator-scoring-requirements-pose-wave-v1.json", "pointer": "/indicators"}]

这三份注册表分别只含6、13、13项FS指标，均无GS。

建议：为GS建立可行性等级、必需特征/阶段、真值/独立测试与标定要求，不推断已有完整GS运行链。

## GS-IMPL-04 40/25/20/15维度权重、GS/FS=70/30、90/80/70/60等级线、ba（需标定）

原文定位：[{"source_id": "CARD-GS", "fields": "A—E; no numeric scores"}]

实现字段：[{"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 104, "symbol_or_text": "DIMENSION_WEIGHTS"}, {"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 111, "symbol_or_text": "MODULE_WEIGHTS"}, {"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 128, "symbol_or_text": "if (score >= 90)"}, {"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 171, "symbol_or_text": "function dimensionScores"}, {"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 249, "symbol_or_text": "function aggregate("}]

40/25/20/15维度权重、GS/FS=70/30、90/80/70/60等级线、baseScore加确定性抖动、指标/事件等权平均全部为demo工程约定，原文无此规则。

建议：只可作为明确标注的演示计算；不得讲成正式规则或由视频实测得到的技术评分。

## GS-IMPL-05 前端用依赖<0.65、0.85/0.15融合、0.72/0.4状态线。源的<70%作用于核（不一致）

原文定位：[{"text": "目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向", "locators": ["CARD-GS:word/document.xml:P0409"], "cells": ["T002.R019.C002"]}]

实现字段：[{"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 166, "symbol_or_text": "scenario.dependencyCoverage[dependency] < 0.65"}, {"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 164, "symbol_or_text": "mandatoryCoverage * 0.85"}, {"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 195, "symbol_or_text": "missing.length === 0 && evidence >= 0.72"}]

前端用依赖<0.65、0.85/0.15融合、0.72/0.4状态线。源的<70%作用于核心人体关键点有效帧；它们不是同一量也不能替代源门槛。

建议：另列原文事件局部门槛与工程证据就绪度；不要把任一证据百分数映射为技术等级。

## GS-IMPL-06 全部248卡原文要求目标track稳定，现有GS dependencies没有一项包含tr（不一致）

原文定位：[{"text": "目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向", "locators": ["CARD-GS:word/document.xml:P0409"], "cells": ["T002.R019.C002"]}, {"text": "目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价", "locators": ["CARD-GS:word/document.xml:P11433"], "cells": ["T249.R019.C002"]}]

实现字段：[{"file": "src/rallymate_scoring/data/metric_cards.json", "pointer": "/cards/*/dependencies"}, {"file": "scoring-demo-web/app/data/metric-cards.json", "pointer": "/cards/*/dependencies"}, {"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 156, "symbol_or_text": "const mandatory = card.dependencies"}]

全部248卡原文要求目标track稳定，现有GS dependencies没有一项包含tracking，engine仅从dependencies构造必需依赖。

建议：把track稳定性作为全局且事件局部强制门槛；不能只有pose高覆盖即宣称证据齐全。

## GS-IMPL-07 真实前端分支返回score/grade=null；后端缺特征返回unavailable，无（一致）

原文定位：[{"text": "目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向", "locators": ["CARD-GS:word/document.xml:P0409"], "cells": ["T002.R019.C002"]}]

实现字段：[{"file": "scoring-demo-web/app/scoring/engine.ts", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\scoring-demo-web\\app\\scoring\\engine.ts", "line": 193, "symbol_or_text": "if (scenario.mode === \"real\")"}, {"file": "src/rallymate_scoring/scoring.py", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\src\\rallymate_scoring\\scoring.py", "line": 271, "symbol_or_text": "if calibration is None:"}, {"file": "src/rallymate_scoring/scoring.py", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\src\\rallymate_scoring\\scoring.py", "line": 216, "symbol_or_text": "status=\"unavailable\""}]

真实前端分支返回score/grade=null；后端缺特征返回unavailable，无标定返回calibration_required，禁止缺测补分。

建议：保留该边界，展示缺测原因、仍需实现/标定的部分。

## GS-IMPL-08 后端可用标定阈值和单位/版本/方向检查产出等级，但此能力不证明GS已有可信标定；GS re（需标定）

原文定位：[{"source_id": "CARD-GS", "fields": "A—E qualitative text"}]

实现字段：[{"file": "src/rallymate_scoring/scoring.py", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\src\\rallymate_scoring\\scoring.py", "line": 475, "symbol_or_text": "if backend == \"threshold_rule\":"}, {"file": "src/rallymate_scoring/scoring.py", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\src\\rallymate_scoring\\scoring.py", "line": 486, "symbol_or_text": "if item.get(\"unit\") != calibration[\"unit\"]:"}, {"file": "src/rallymate_scoring/scoring.py", "snapshot_file": ".codex_tmp\\scoring-reference-20261004\\implementation-snapshot\\src\\rallymate_scoring\\scoring.py", "line": 495, "symbol_or_text": "if calibration[\"direction\"] == \"higher_is_better\":"}]

后端可用标定阈值和单位/版本/方向检查产出等级，但此能力不证明GS已有可信标定；GS requirements缺失。

建议：仅经合法注册、单位一致、教练真值和独立测试后启用，记录threshold_version；不要把文档定性描述伪装数值标定。

## GS-IMPL-09 技术注册表包含相符5种底线技术但每项为五阶段；CARD为10阶段，二者是不同粒度，尚无正式（mapping_required）

原文定位：[{"source_id": "CARD-GS", "stages_per_event": 10}]

实现字段：[{"file": "src/rallymate_scoring/data/technique_metrics.json", "pointer": "/techniques"}, {"file": "scoring-demo-web/app/data/technique-catalog.json", "pointer": "/techniques"}]

技术注册表包含相符5种底线技术但每项为五阶段；CARD为10阶段，二者是不同粒度，尚无正式逐阶段等价映射。

建议：名称建立可审阅跨表链接；阶段映射需单列多对多及不确定边界，不能改写原指标编号。

## GS-IMPL-10 原文异常同样进入两份注册表；一致性通过不等于内容正确。（source_conflict_preserved）

原文定位：[{"conflict_ids": ["GS-CONFLICT-01", "GS-CONFLICT-02", "GS-CONFLICT-03"]}]

实现字段：[{"file": "src/rallymate_scoring/data/metric_cards.json", "pointer": "/cards"}, {"file": "scoring-demo-web/app/data/metric-cards.json", "pointer": "/cards"}]

原文异常同样进入两份注册表；一致性通过不等于内容正确。

建议：引入source_review_required状态并绑定冲突ID；源修订前停止相关正式评分或技术纠正断言。

## VIS-C01 网前与高压可选阶段未结构化（未实现）

原文定位：[{"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0023", "locator": "VIS-NET:word/document.xml:P0023", "part": "word/document.xml", "paragraph": 23, "table_cell": null, "xpath": "/w:document/w:body/w:p[11]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0069", "locator": "VIS-NET:word/document.xml:P0069", "part": "word/document.xml", "paragraph": 69, "table_cell": null, "xpath": "/w:document/w:body/w:p[27]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0124", "locator": "VIS-NET:word/document.xml:P0124", "part": "word/document.xml", "paragraph": 124, "table_cell": null, "xpath": "/w:document/w:body/w:p[46]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 194, "json_pointer": "/techniques/11/phases", "technique_id": "forehand_volley", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/11/phases"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 205, "json_pointer": "/techniques/12/phases", "technique_id": "backhand_volley", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/12/phases"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 216, "json_pointer": "/techniques/13/phases", "technique_id": "overhead", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/13/phases"}]

原文明确允许正/反截击稳定调整、高压蓄力阶段没有；当前 phases 仅字符串数组，无 optional/applicability。不能把阶段未检出当动作缺失扣分。

建议：为这三个阶段添加 conditional/optional 与 not_applicable；回放解释保留快速来球等上下文，不进入缺项分母。

## VIS-C02 GS 五阶段与评分卡十阶段并存（需标定）

原文定位：[{"id": "VIS-GS@044d3643b0e9:word/document.xml:P0002", "locator": "VIS-GS:word/document.xml:P0002", "part": "word/document.xml", "paragraph": 2, "table_cell": null, "xpath": "/w:document/w:body/w:p[2]"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0031", "locator": "VIS-GS:word/document.xml:P0031", "part": "word/document.xml", "paragraph": 31, "table_cell": "T003.R002.C002", "xpath": "/w:document/w:body/w:tbl[3]/w:tr[2]/w:tc[2]/w:p"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "json_pointer": "/default_phase_contracts/baseline"}, {"path": "src/rallymate_scoring/data/metric_cards.json", "json_pointer": "/cards/*/stageCode"}]

VIS-GS 为 observation/preparation/stability/strike/recovery；metric_cards 的每个 GS 事件有 M01 至 M10；且盯球贯穿全程而非互斥片段。不能静默合并、平均或互相替换。

建议：保留两套 contract；建立 many-to-many 来源映射。稳定调整跨引拍末到挥拍前；盯球是全程属性，不能按五个互斥等权片段评分。

## VIS-C03 1.8 倍参考与未定量加分保留为参考（一致）

原文定位：[{"id": "VIS-SV@a8b822829947:word/document.xml:P0020", "locator": "VIS-SV:word/document.xml:P0020", "part": "word/document.xml", "paragraph": 20, "table_cell": "T002.R003.C002", "xpath": "/w:document/w:body/w:tbl[2]/w:tr[3]/w:tc[2]/w:p[1]"}, {"id": "VIS-SV@a8b822829947:word/document.xml:P0048", "locator": "VIS-SV:word/document.xml:P0048", "part": "word/document.xml", "paragraph": 48, "table_cell": "T005.R003.C002", "xpath": "/w:document/w:body/w:tbl[5]/w:tr[3]/w:tc[2]/w:p"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 110, "json_pointer": "/techniques/5/reference_constraints", "technique_id": "serve", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/5/reference_constraints"}]

1.8 为约数、没有公差及评分分段；“加分项目”没有分值。registry 正确声明 visual_reference_only_not_scoring_threshold 与 optional_reference_only_no_numeric_bonus。

建议：显示“原文参考约 1.8×身高/落地抬腿为可选正向观察”；不可生成 100 分阈值或 +5 分。

## VIS-C04 每条来源定位不足（未实现）

原文定位：[{"id": "VIS-FS@bbab99f52199:word/document.xml:P0014", "locator": "VIS-FS:word/document.xml:P0014", "part": "word/document.xml", "paragraph": 14, "table_cell": "T001.R003.C002", "xpath": "/w:document/w:body/w:tbl[1]/w:tr[3]/w:tc[2]/w:p[5]"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0024", "locator": "VIS-GS:word/document.xml:P0024", "part": "word/document.xml", "paragraph": 24, "table_cell": "T002.R003.C002", "xpath": "/w:document/w:body/w:tbl[2]/w:tr[3]/w:tc[2]/w:p"}, {"id": "VIS-RT@11ba48b880f2:word/document.xml:P0036", "locator": "VIS-RT:word/document.xml:P0036", "part": "word/document.xml", "paragraph": 36, "table_cell": null, "xpath": "/w:document/w:body/w:p[36]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "json_pointer": "/source_documents"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "json_pointer": "/techniques/*/core_visual_features"}]

source_documents 有五个原文件名和正确 SHA256；技术摘要没有段落/表格定位和每条规则证据引用。

建议：使用本交接 JSON 的稳定文档 ID、paragraph/table_cell/xpath 和规则 ID；在解释中显示原文与观测两侧依据。

## VIS-C05 证据完备参考范围不是原文 100 分技术评分（需标定）

原文定位：[{"id": "VIS-FS@bbab99f52199:word/document.xml:P0002", "locator": "VIS-FS:word/document.xml:P0002", "part": "word/document.xml", "paragraph": 2, "table_cell": null, "xpath": "/w:document/w:body/w:p[2]"}, {"id": "VIS-SV@a8b822829947:word/document.xml:P0002", "locator": "VIS-SV:word/document.xml:P0002", "part": "word/document.xml", "paragraph": 2, "table_cell": null, "xpath": "/w:document/w:body/w:p[2]"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0002", "locator": "VIS-GS:word/document.xml:P0002", "part": "word/document.xml", "paragraph": 2, "table_cell": null, "xpath": "/w:document/w:body/w:p[2]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0002", "locator": "VIS-NET:word/document.xml:P0002", "part": "word/document.xml", "paragraph": 2, "table_cell": null, "xpath": "/w:document/w:body/w:p[2]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 37, "json_pointer": "/semantics"}]

registry score_range=[0,100] 与 evidence_readiness_reference 属工程合同；五视觉文档未给 0–100 的转换、阈值、权重、分段或总等级。formal_coach_score=false 正確保持边界。

建议：将观测完备度和技术质量分栏；不得称证据 100 分为动作 100 分，也不能用缺测反推扣分。

## VIS-C06 接发相对幅度规则需要所有子类继承（需标定）

原文定位：[{"id": "VIS-RT@11ba48b880f2:word/document.xml:P0036", "locator": "VIS-RT:word/document.xml:P0036", "part": "word/document.xml", "paragraph": 36, "table_cell": null, "xpath": "/w:document/w:body/w:p[36]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 139, "json_pointer": "/techniques/6/proxy_limits", "technique_id": "return_forehand", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/6/proxy_limits"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 150, "json_pointer": "/techniques/7/proxy_limits", "technique_id": "return_two_hand_backhand", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/7/proxy_limits"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 161, "json_pointer": "/techniques/8/proxy_limits", "technique_id": "return_one_hand_backhand", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/8/proxy_limits"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 172, "json_pointer": "/techniques/9/proxy_limits", "technique_id": "return_backhand_slice", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/9/proxy_limits"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 183, "json_pointer": "/techniques/10/proxy_limits", "technique_id": "return_forehand_slice", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/10/proxy_limits"}]

原文要求相对动作幅度和时间，而不是固定绝对阈值。registry 仅双反接发写了“无法确认发球速度时”使用相对时序，条件比原文更窄；其余子类未显式继承。

建议：在 return family 放共同约束并由全部五子类继承；速度可测也需按时间压力标定，不能自动启用统一固定阈值。

## VIS-C07 代理观测边界保留（一致）

原文定位：[{"id": "VIS-GS@044d3643b0e9:word/document.xml:P0010", "locator": "VIS-GS:word/document.xml:P0010", "part": "word/document.xml", "paragraph": 10, "table_cell": "T001.R002.C002", "xpath": "/w:document/w:body/w:tbl[1]/w:tr[2]/w:tc[2]/w:p"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0040", "locator": "VIS-GS:word/document.xml:P0040", "part": "word/document.xml", "paragraph": 40, "table_cell": "T004.R002.C002", "xpath": "/w:document/w:body/w:tbl[4]/w:tr[2]/w:tc[2]/w:p"}, {"id": "VIS-SV@a8b822829947:word/document.xml:P0018", "locator": "VIS-SV:word/document.xml:P0018", "part": "word/document.xml", "paragraph": 18, "table_cell": "T002.R002.C002", "xpath": "/w:document/w:body/w:tbl[2]/w:tr[2]/w:tc[2]/w:p"}, {"id": "VIS-SV@a8b822829947:word/document.xml:P0037", "locator": "VIS-SV:word/document.xml:P0037", "part": "word/document.xml", "paragraph": 37, "table_cell": "T004.R002.C002", "xpath": "/w:document/w:body/w:tbl[4]/w:tr[2]/w:tc[2]/w:p"}, {"id": "VIS-RT@11ba48b880f2:word/document.xml:P0044", "locator": "VIS-RT:word/document.xml:P0044", "part": "word/document.xml", "paragraph": 44, "table_cell": null, "xpath": "/w:document/w:body/w:p[44]"}, {"id": "VIS-FS@bbab99f52199:word/document.xml:P0139", "locator": "VIS-FS:word/document.xml:P0139", "part": "word/document.xml", "paragraph": 139, "table_cell": "T010.R003.C002", "xpath": "/w:document/w:body/w:tbl[10]/w:tr[3]/w:tc[2]/w:p[5]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "json_pointer": "/semantics"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "json_pointer": "/techniques/*/proxy_limits"}]

registry 全局声明头部/连续响应仅代理、真实球拍触球需要可靠球/拍轨迹、缺证据 unavailable；与来源一致。

建议：运行结果逐项实现这些边界，不能仅有静态声明；视线、抛球、触球、战术最优等须单独 unavailable。

## VIS-C08 原文随挥速度措辞需时间窗口澄清（需标定）

原文定位：[{"id": "VIS-GS@044d3643b0e9:word/document.xml:P0047", "locator": "VIS-GS:word/document.xml:P0047", "part": "word/document.xml", "paragraph": 47, "table_cell": "T005.R001.C002", "xpath": "/w:document/w:body/w:tbl[5]/w:tr[1]/w:tc[2]/w:p"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0051", "locator": "VIS-GS:word/document.xml:P0051", "part": "word/document.xml", "paragraph": 51, "table_cell": "T005.R003.C002", "xpath": "/w:document/w:body/w:tbl[5]/w:tr[3]/w:tc[2]/w:p"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0102", "locator": "VIS-GS:word/document.xml:P0102", "part": "word/document.xml", "paragraph": 102, "table_cell": "T010.R001.C002", "xpath": "/w:document/w:body/w:tbl[10]/w:tr[1]/w:tc[2]/w:p"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0106", "locator": "VIS-GS:word/document.xml:P0106", "part": "word/document.xml", "paragraph": 106, "table_cell": "T010.R003.C002", "xpath": "/w:document/w:body/w:tbl[10]/w:tr[3]/w:tc[2]/w:p[1]"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0107", "locator": "VIS-GS:word/document.xml:P0107", "part": "word/document.xml", "paragraph": 107, "table_cell": "T010.R003.C002", "xpath": "/w:document/w:body/w:tbl[10]/w:tr[3]/w:tc[2]/w:p[2]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 55, "json_pointer": "/techniques/0/core_visual_features", "technique_id": "baseline_forehand", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/0/core_visual_features"}]

定义要求随挥逐渐减速，关键点同时写“击球后没有明显降速”、正手“搞阶段甚至加速”；可能分别指即时延续与后程减速，但原文未给窗口。

建议：原文并列保留，待作者澄清“搞阶段”与时间窗口；不可静默改写成固定加速/减速分数。

## VIS-C09 源文复制/未完成语句（需标定）

原文定位：[{"id": "VIS-GS@044d3643b0e9:word/document.xml:P0114", "locator": "VIS-GS:word/document.xml:P0114", "part": "word/document.xml", "paragraph": 114, "table_cell": "T011.R001.C002", "xpath": "/w:document/w:body/w:tbl[11]/w:tr[1]/w:tc[2]/w:p"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0160", "locator": "VIS-GS:word/document.xml:P0160", "part": "word/document.xml", "paragraph": 160, "table_cell": "T016.R001.C002", "xpath": "/w:document/w:body/w:tbl[16]/w:tr[1]/w:tc[2]/w:p"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0207", "locator": "VIS-GS:word/document.xml:P0207", "part": "word/document.xml", "paragraph": 207, "table_cell": "T021.R001.C002", "xpath": "/w:document/w:body/w:tbl[21]/w:tr[1]/w:tc[2]/w:p"}, {"id": "VIS-FS@bbab99f52199:word/document.xml:P0081", "locator": "VIS-FS:word/document.xml:P0081", "part": "word/document.xml", "paragraph": 81, "table_cell": "T006.R003.C002", "xpath": "/w:document/w:body/w:tbl[6]/w:tr[3]/w:tc[2]/w:p[2]"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0085", "locator": "VIS-GS:word/document.xml:P0085", "part": "word/document.xml", "paragraph": 85, "table_cell": "T008.R003.C002", "xpath": "/w:document/w:body/w:tbl[8]/w:tr[3]/w:tc[2]/w:p[1]"}, {"id": "VIS-RT@11ba48b880f2:word/document.xml:P0045", "locator": "VIS-RT:word/document.xml:P0045", "part": "word/document.xml", "paragraph": 45, "table_cell": null, "xpath": "/w:document/w:body/w:p[45]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 66, "json_pointer": "/techniques/1/core_visual_features", "technique_id": "baseline_two_hand_backhand", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/1/core_visual_features"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 77, "json_pointer": "/techniques/2/core_visual_features", "technique_id": "baseline_one_hand_backhand", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/2/core_visual_features"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 88, "json_pointer": "/techniques/3/core_visual_features", "technique_id": "backhand_slice", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/3/core_visual_features"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 282, "json_pointer": "/techniques/19/core_visual_features", "technique_id": "open_stance", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/19/core_visual_features"}]

双反/单反/反手切削“盯球是什么”仍写“正手击球”；FS 开放支撑“重心压于击球”句子未完，GS 有“击球测”、RT 有“会拍”等字样。

建议：原样保留并标疑；可按对应章节关联技术身份，但不能把推定订正冒充源文或产生新数值规则。

## VIS-C10 高压专属阶段不能使用网前默认列表覆盖（未实现）

原文定位：[{"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0097", "locator": "VIS-NET:word/document.xml:P0097", "part": "word/document.xml", "paragraph": 97, "table_cell": null, "xpath": "/w:document/w:body/w:p[37]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0106", "locator": "VIS-NET:word/document.xml:P0106", "part": "word/document.xml", "paragraph": 106, "table_cell": null, "xpath": "/w:document/w:body/w:p[40]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0115", "locator": "VIS-NET:word/document.xml:P0115", "part": "word/document.xml", "paragraph": 115, "table_cell": null, "xpath": "/w:document/w:body/w:p[43]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0124", "locator": "VIS-NET:word/document.xml:P0124", "part": "word/document.xml", "paragraph": 124, "table_cell": null, "xpath": "/w:document/w:body/w:p[46]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0133", "locator": "VIS-NET:word/document.xml:P0133", "part": "word/document.xml", "paragraph": 133, "table_cell": null, "xpath": "/w:document/w:body/w:p[49]"}, {"id": "VIS-NET@a7ddfe87a916:word/document.xml:P0142", "locator": "VIS-NET:word/document.xml:P0142", "part": "word/document.xml", "paragraph": 142, "table_cell": null, "xpath": "/w:document/w:body/w:p[52]"}]

实现字段：[{"path": "src/rallymate_scoring/data/technique_metrics.json", "json_pointer": "/default_phase_contracts/net_attack"}, {"path": "src/rallymate_scoring/data/technique_metrics.json", "line": 216, "json_pointer": "/techniques/13/phases", "technique_id": "overhead", "frontend_path": "scoring-demo-web/app/data/technique-catalog.json", "frontend_json_pointer": "/techniques/13/phases"}]

默认 net_attack 为五阶段，overhead 已正確设置独立六阶段；数据可表达特例，但默认合同缺少“逐技术覆盖优先”的结构约束。

建议：消费者优先 technique.phases；高压保留定位→trophy→可选 stability，不能套截击五阶段。

## VIS-C11 GS/FS正式卡与视觉文档不可互相当作评分依据（未实现）

原文定位：[{"id": "VIS-FS@bbab99f52199:word/document.xml:P0002", "locator": "VIS-FS:word/document.xml:P0002", "part": "word/document.xml", "paragraph": 2, "table_cell": null, "xpath": "/w:document/w:body/w:p[2]"}, {"id": "VIS-GS@044d3643b0e9:word/document.xml:P0002", "locator": "VIS-GS:word/document.xml:P0002", "part": "word/document.xml", "paragraph": 2, "table_cell": null, "xpath": "/w:document/w:body/w:p[2]"}]

实现字段：[{"path": "src/rallymate_scoring/data/metric_cards.json", "json_pointer": "/cards/*/grades"}, {"path": "src/rallymate_scoring/data/metric_cards.json", "json_pointer": "/cards/*/unavailable"}]

metric_cards 有298项(A–E、可评分状态及70%等条件)。这些值不能从五份视觉文档验证，只能回溯另两份 GS/FS 评分卡；本子审计未对两份卡片原文作判断。

建议：前端每条等级/不可用阈值必须附对应 CARD 来源；禁止给发球/接发/网前套用 GS/FS 通用 A–E 文案而声称视觉文档原定。

## 缺失要求

- MISSING-01：实测特征到A-E的数值切点、方向、容差或多变量模型。来源状态：未给出；有定性行为等级。下一步：教练标注与独立测试，生成按指标/版本绑定的生产标定资产。
- MISSING-02：A-E到0-100映射及分数可辨精度。来源状态：未给出。下一步：如需100分另立评分量表；不能从现有定性等级倒推出分数。
- MISSING-03：权重、满分、扣分项、累加/封顶/重复扣分与总分聚合。来源状态：未给出。下一步：另立事件/阶段/模块聚合规范并明确其属于新增工程/教练规则。
- MISSING-04：计算描述的特征单位、坐标、平滑、窗口、阈值、帧率归一化与方向契约。来源状态：仅描述角度、速度、归一化距离等；尚不构成完整可执行定义。下一步：逐指标签认可测特征、代理局限及单位；不得直接使用像素阈值跨视频比较。
- MISSING-05：有效帧定义与分母、要求关节集、事件关联、身份连续性和未知状态。来源状态：明确70%不可评价条件但没有完整实施细则；部分FS卡另有视角或专项条件。下一步：按卡条件建立门禁，缺测null并呈现原因；E级只可在足够观测下判定行为未出现。
- MISSING-06：事件真值、完整阶段边界、观测视角、专项球拍/球轨迹与场地标定。来源状态：动作定义不能证明视频内动作或阶段已发生。下一步：事件切分和测量验证；无事件时显示未观测而非0分。
- MISSING-07：标注者一致性、教练真值、独立测试、适用人群与场景。来源状态：七份Word均未提供数据或经验证的模型。下一步：建立版本化标注/校准/独立测试集，按F2→F3→F4晋级，不伪造真值。
- MISSING-08：新旧版本覆盖关系、源内错项的正式修订和来源授权。来源状态：未见正式废止/替代声明；部分源文本名称或定义冲突。下一步：保留所有来源及待决记录，采用明确的冲突状态，后续经源维护者确认后发布新版本。

## 前端验收要求

- UI-01：每一数值必须显示类型：演示分、证据覆盖率、证据就绪度、实测特征或正式评级。验收：真实视频结果不使用演示baseScore/hash维度；coverage不写准确率/技术分。
- UI-02：可展开原文依据、稳定source_id/SHA、段落/表格定位、指标ID及版本。验收：点击指标可定位source_fields；新旧文档不共用模糊来源名称。
- UI-03：给出观测事件时间段、所需关键点/依赖、实测值、单位、质量门禁与代理限制。验收：阶段proxy、候选动作、检测框与真实触球/发力/视线分开命名。
- UI-04：缺测、未观测、尚未实现、待标定、质量阻断、源规则冲突分别显示。验收：score/grade保持null；不显示0分、E级、扣分或动作差；单个不可测子项不自动否定可测代理部分。
- UI-05：定性等级可作为规则说明，实际授级必须来自通过门禁的生产标定结果。验收：显示threshold/model/registry/runtime版本与证据；sourceStatus可评分不得用作本次scored状态。
- UI-06：原文未定义100分与扣分，当前正式评分区说明尚未建立数值量表。验收：不解释为何从100扣到某值；如保留演示数值，清晰声明合成来源与演示权重。
- UI-07：证据就绪度显示必需与增强证据贡献及作用域，缺失项显示需要补什么证据。验收：70/30工程权重和0.40/0.72工程门槛明确可见；不得与原文70%有效帧混用。
- UI-08：1.8倍左右抛球高度显示参考值及测量前提，不生成机械加减分。验收：没有允许偏差、球高度标定和个体基准时不判定合格范围。
- UI-09：汇总给出已观测/已测量/可正式评级的分母和未覆盖项。验收：不把未拍摄的发球/接发/网前按0计入总分；未建正式聚合时整体分与等级为空。
