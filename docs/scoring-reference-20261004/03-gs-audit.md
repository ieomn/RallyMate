# GS 全指标规则核验 2026-10-04

本审计完整覆盖 GS 评分卡的 5 类底线技术、50 阶段、248 项正式指标，逐字段保留原文定位。该材料不足以直接给出可核验的 100 分制技术分、扣分或自动 A—E 等级。A—E 定性描述、证据要求和技术动作目录可作人工参考；模型必须在事件识别、可观测特征和教练标定齐备后才进入正式评级。

源文件：GS底线基础事件评分指标卡_正确版.docx
SHA256：151f4a551806fa3d1e5f4db3310efcf51c844b2c19266d283cc9a6dde0f416e0
稳定ID：CARD-GS@151f4a551806

引用使用原文件 word/document.xml 段落号及物理表格行列；不推断Word页码。实现核验基于 implementation-snapshot 快照，主对话随后改动不纳入本轮断言。

## 核验结果

- GS01/GS02/GS03/GS04/GS05 分别 51/49/49/49/50 项。
- 原文状态：可评分136、部分可评分80、条件可评分22、暂不可评分10。状态不代表当前实现已有同样能力。
- 248项全部有核心人体关键点有效帧低于70%不可评价；70%不是技术及格线。
- 20阶段、97指标的开始和结束动作均为空；51项没有独立原文关键项。
- 两份现有metric注册表字节相同；248×19×2=9424个原文值逐字一致。该一致性也复制了源错误。
- 248项在measurement-plans的事件定位全部未实现；162项主要阻塞为专项目标依赖、86项为事件实现。可行性6项/13项及评分requirements13项均为FS，GS为0项。

## 原文明确规则与缺口

### GS-R-01

目标球员 track_id 不稳定，或核心人体关键点有效帧低于70%，不可评价。

原文未定义有效帧置信度阈值、核心点子集、统计分母/窗口、连续缺口或track稳定性算法；70%是证据门槛，不是技术C级线。

### GS-R-02

每卡提供A/B/C/D/E五档定性描述。

没有分数区间、动作幅度/时序数值边界、教练一致性数据或等级校准模型。

### GS-R-03

3. 当前视觉状态依据第一阶段工程能力：稳定目标球员 track_id、COCO-17 人体关键点、时间戳，以及当前球/球拍/场地的有限检测能力。真实视线、足底接触、真实重心、地面反作用力、拍面三维姿态、甜区、稳定活动球轨迹和精确触球事件不得由第一阶段模型臆测。



### GS-R-04

球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价。

没有定义剩余可观测部分能否赋整卡等级及其重新加权，不能自动把缺测当E或0分。

- GS-MISS-UNIT：每项位移/速度/角度/时序的数值单位、坐标系、归一化分母、时间窗、平滑与精度要求。状态：not_specified_in_source。
- GS-MISS-THRESHOLD：每项A—E的数值阈值或可信分类模型；较大越好/较小越好/目标区间等标量方向。状态：not_specified_in_source。
- GS-MISS-SCORE：0—100分映射、起始分、逐项扣分、重复错误扣分上限、指标/阶段/事件权重、跨事件/模块聚合公式。状态：not_specified_in_source。
- GS-MISS-GROUNDTRUTH：目标球员与持拍侧、事件与阶段真值、关键帧、教练等级标签、一致性与独立测试、适用机位/帧率条件。状态：requires_collection_and_calibration。
- GS-MISS-GATE：核心关键点、有效帧/track稳定定义；把全局覆盖率转为本事件本指标覆盖率的规则。状态：partially_specified_only_70_percent_and_tracking_condition。
- GS-MISS-BOUNDARY：97卡/20阶段开始与结束动作空白。状态：source_blank。
- GS-MISS-INDEPENDENT-DEF：独立技术定义/关键项缺失或源声明只有阶段行为。状态：source_explicitly_absent。
- GS-MISS-PHASE-CROSSWALK：旧评分卡10阶段到五阶段视觉定义的正式逐项跨版本映射。状态：not_implemented_or_source_defined。

## 源内冲突和优先级

### GS-CONFLICT-01 技术定义混入下一节标题

调整身体姿态并建立下一拍准备状态的定义为“GS-02 双手反手”。前后端完整复制了此源异常。

受影响：GS01-M10-04。

建议：保留原值与异常标志；由规则所有者修订并给新版本，在此之前该定义不可作为自动评分/建议依据。

状态：未决；不得静默择一。

- GS01-M10-04 指标名称 [CARD-GS:word/document.xml:P2604 / T052.R003.C002]：调整身体姿态并建立下一拍准备状态
- GS01-M10-04 技术定义 [CARD-GS:word/document.xml:P2614 / T052.R008.C002]：GS-02 双手反手
- GS01-M10-04 原文关键项 [CARD-GS:word/document.xml:P2616 / T052.R009.C002]：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据

### GS-CONFLICT-02 双反准备相邻两卡名称和定义疑似对调

“盯球观察”定义是双手握拍；“双手建立协调握拍”定义是观察来球。当前识别点/反馈又混合名称和定义语义，不能仅交换名称就视为修复。

受影响：GS02-M01-01, GS02-M01-02。

建议：暂停这两卡的语义评分映射，向规则所有者核定ID、名称、定义、关键项及观测依赖，保存显式迁移记录。

状态：未决；不得静默择一。

- GS02-M01-01 指标名称 [CARD-GS:word/document.xml:P2652 / T053.R003.C002]：盯球观察
- GS02-M01-01 技术定义 [CARD-GS:word/document.xml:P2662 / T053.R008.C002]：建立稳定的双手握拍关系，双手共同控制球拍，形成双手反手准备握拍姿态。
- GS02-M01-01 原文关键项 [CARD-GS:word/document.xml:P2664 / T053.R009.C002]：双手保持合理间距；双手稳定控制拍柄；球拍保持身体前方；拍面保持稳定；为双手共同引拍创造条件
- GS02-M01-02 指标名称 [CARD-GS:word/document.xml:P2696 / T054.R003.C002]：双手建立协调握拍
- GS02-M01-02 技术定义 [CARD-GS:word/document.xml:P2706 / T054.R008.C002]：持续观察来球位置、速度及飞行方向，建立击球信息输入。
- GS02-M01-02 原文关键项 [CARD-GS:word/document.xml:P2708 / T054.R009.C002]：头部稳定；视线跟踪来球；持续观察球飞行轨迹；判断来球高度；判断来球速度及方向

### GS-CONFLICT-03 复用阶段观测与状态变动没有解释

GS05-M01宣称复用GS01-M01，但上述四项改变了观测点/不可评价条件，且从部分可评分变为可评分。名称和动作语义相同不能推导来源状态优先级。

受影响：GS01-M01-01, GS01-M01-03, GS01-M01-04, GS01-M01-05, GS05-M01-01, GS05-M01-03, GS05-M01-04, GS05-M01-05。

建议：保留两套来源，不因复用覆盖原条件；将头部观察与手脚动作的依赖分别核定。

状态：未决；不得静默择一。

- GS01-M01-01 来源/复用 [CARD-GS:word/document.xml:P0381 / T002.R005.C002]：原文阶段行为（无独立行为定义，不补写新的网球技术事实）
- GS01-M01-01 当前识别点 [CARD-GS:word/document.xml:P0391 / T002.R010.C002]：双肘/双腕轨迹、速度、相对位置与连续性、头部位置与朝向稳定度；真实视线仅作近似
- GS01-M01-01 所需点 [CARD-GS:word/document.xml:P0393 / T002.R011.C002]：J101/J121；J103/J123；J004；J006/J007
- GS01-M01-01 当前状态 [CARD-GS:word/document.xml:P0395 / T002.R012.C002]：部分可评分
- GS01-M01-01 不可评价 [CARD-GS:word/document.xml:P0409 / T002.R019.C002]：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向
- GS01-M01-03 来源/复用 [CARD-GS:word/document.xml:P0469 / T004.R005.C002]：原文阶段行为（无独立行为定义，不补写新的网球技术事实）

### GS-CONFLICT-04 完成垫步指标仅列头/眼所需点

“完成垫步”仅列J004/J006/J007及头部稳定代理；不能支持双脚离地/落地事件判定。

受影响：GS01-M01-04。

建议：按技术定义补充下肢及落地证据要求属于待核定修订，不能声称头部变化已证明垫步。

状态：未决；不得静默择一。

- GS01-M01-04 指标名称 [CARD-GS:word/document.xml:P0509 / T005.R003.C002]：完成垫步
- GS01-M01-04 所需点 [CARD-GS:word/document.xml:P0525 / T005.R011.C002]：J004；J006/J007
- GS01-M01-04 当前识别点 [CARD-GS:word/document.xml:P0523 / T005.R010.C002]：头部位置与朝向稳定度；真实视线仅作近似
- GS01-M01-04 计算方式 [CARD-GS:word/document.xml:P0529 / T005.R013.C002]：人体关键点时序变化 + 动作连续性

### GS-CONFLICT-05 来源可评分状态不能视为当前完整可评分

这些卡写“可评分”，同时计算方式包含尚待专项球拍模型/稳定活动球的条件。源状态描述粗略，未定义可评分的局部范围。

受影响：GS01-M02-01, GS01-M02-02, GS01-M02-04, GS01-M03-03, GS01-M05-01, GS01-M06-01, GS01-M06-04, GS01-M06-05, GS01-M07-02, GS01-M09-01, GS01-M09-03, GS01-M09-04, GS01-M09-05, GS01-M10-02, GS02-M01-03, GS02-M01-05, GS02-M02-01, GS02-M02-02, GS02-M02-04, GS02-M03-05, GS02-M04-04, GS02-M05-01, GS02-M05-04, GS02-M06-01, GS02-M06-03, GS02-M06-04, GS02-M07-04, GS02-M08-01, GS02-M09-01, GS02-M09-03, GS02-M10-01, GS02-M10-02, GS02-M10-04, GS03-M01-04, GS03-M02-01, GS03-M02-02, GS03-M02-04, GS03-M03-01, GS03-M03-05, GS03-M05-05, GS03-M06-01, GS03-M06-04, GS03-M07-04, GS03-M09-03, GS03-M09-05, GS03-M10-01, GS03-M10-03, GS03-M10-05, GS04-M01-04, GS04-M02-01, GS04-M02-02, GS04-M02-04, GS04-M06-04, GS04-M07-01, GS04-M07-04, GS04-M10-01, GS04-M10-05, GS05-M02-01, GS05-M02-02, GS05-M02-04, GS05-M06-04, GS05-M07-01, GS05-M07-04, GS05-M09-04, GS05-M10-01, GS05-M10-05。

建议：UI显示“原文状态”与“本次可观测部分/未实现/需标定”两个字段；逐原子行为说明限制，不给整卡正式等级。

状态：未决；不得静默择一。

- GS01-M02-01 当前状态 [CARD-GS:word/document.xml:P0618 / T007.R012.C002]：可评分
- GS01-M02-01 计算方式 [CARD-GS:word/document.xml:P0620 / T007.R013.C002]：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后）
- GS01-M02-01 不可评价 [CARD-GS:word/document.xml:P0632 / T007.R019.C002]：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价
- GS01-M02-02 当前状态 [CARD-GS:word/document.xml:P0662 / T008.R012.C002]：可评分
- GS01-M02-02 计算方式 [CARD-GS:word/document.xml:P0664 / T008.R013.C002]：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后）
- GS01-M02-02 不可评价 [CARD-GS:word/document.xml:P0676 / T008.R019.C002]：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价

### GS-CONFLICT-06 正向反馈模板可能超出物理可观测范围

反馈模板声称地面反作用力建立/背部收紧完成，而同卡不可评价条款和附录明确不可直接测量真实肌力/地面反作用力。

受影响：GS01-M04-04, GS03-M04-04, GS03-M09-04。

建议：原文模板仅作审阅素材，面向用户反馈改写为可见的关节动作/稳定性代理；无观测时不输出缺点。

状态：未决；不得静默择一。

- GS01-M04-04 AI正向反馈 [CARD-GS:word/document.xml:P1212 / T020.R020.C002]：建立地面反作用力完成较完整，动作连续性和身体控制较好。
- GS01-M04-04 AI改进反馈 [CARD-GS:word/document.xml:P1214 / T020.R021.C002]：建立地面反作用力表现不足，建议优先按照该阶段原文关键项逐项调整。
- GS01-M04-04 不可评价 [CARD-GS:word/document.xml:P1210 / T020.R019.C002]：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重
- GS03-M04-04 AI正向反馈 [CARD-GS:word/document.xml:P5631 / T119.R020.C002]：建立地面反作用力完成较完整，动作连续性和身体控制较好。
- GS03-M04-04 AI改进反馈 [CARD-GS:word/document.xml:P5633 / T119.R021.C002]：建立地面反作用力表现不足，建议优先按照该阶段原文关键项逐项调整。
- GS03-M04-04 不可评价 [CARD-GS:word/document.xml:P5629 / T119.R019.C002]：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重

## 实现一致性差异

### GS-IMPL-01 consistent

248卡的14个平面原文字段及A—E共19个值与原文逐字相同；两份注册表字节相同。

建议：在不改变原值的基础上增加SHA256、段落/表格定位及源异常标记。

- src/rallymate_scoring/data/metric_cards.json /cards
- scoring-demo-web/app/data/metric-cards.json /cards

### GS-IMPL-02 not_implemented

全部248项 event_localization.status=not_implemented；runtime_status为162项dependency_implementation_required及86项event_implementation_required。

建议：逐阶段补事件切分、证据与真实值；不能把全片检测覆盖率当事件已发生。

- metric-measurement-plans.json /plans

### GS-IMPL-03 not_implemented

这三份注册表分别只含6、13、13项FS指标，均无GS。

建议：为GS建立可行性等级、必需特征/阶段、真值/独立测试与标定要求，不推断已有完整GS运行链。

- metric-feasibility.json /indicators
- metric-feasibility-pose-wave-v2.json /indicators
- indicator-scoring-requirements-pose-wave-v1.json /indicators

### GS-IMPL-04 source_not_provided

40/25/20/15维度权重、GS/FS=70/30、90/80/70/60等级线、baseScore加确定性抖动、指标/事件等权平均全部为demo工程约定，原文无此规则。

建议：只可作为明确标注的演示计算；不得讲成正式规则或由视频实测得到的技术评分。

- scoring-demo-web/app/scoring/engine.ts:104 DIMENSION_WEIGHTS
- scoring-demo-web/app/scoring/engine.ts:111 MODULE_WEIGHTS
- scoring-demo-web/app/scoring/engine.ts:128 if (score >= 90)
- scoring-demo-web/app/scoring/engine.ts:171 function dimensionScores
- scoring-demo-web/app/scoring/engine.ts:249 function aggregate(

### GS-IMPL-05 inconsistent

前端用依赖<0.65、0.85/0.15融合、0.72/0.4状态线。源的<70%作用于核心人体关键点有效帧；它们不是同一量也不能替代源门槛。

建议：另列原文事件局部门槛与工程证据就绪度；不要把任一证据百分数映射为技术等级。

- scoring-demo-web/app/scoring/engine.ts:166 scenario.dependencyCoverage[dependency] < 0.65
- scoring-demo-web/app/scoring/engine.ts:164 mandatoryCoverage * 0.85
- scoring-demo-web/app/scoring/engine.ts:195 missing.length === 0 && evidence >= 0.72

### GS-IMPL-06 inconsistent

全部248卡原文要求目标track稳定，现有GS dependencies没有一项包含tracking，engine仅从dependencies构造必需依赖。

建议：把track稳定性作为全局且事件局部强制门槛；不能只有pose高覆盖即宣称证据齐全。

- src/rallymate_scoring/data/metric_cards.json /cards/*/dependencies
- scoring-demo-web/app/data/metric-cards.json /cards/*/dependencies
- scoring-demo-web/app/scoring/engine.ts:156 const mandatory = card.dependencies

### GS-IMPL-07 consistent

真实前端分支返回score/grade=null；后端缺特征返回unavailable，无标定返回calibration_required，禁止缺测补分。

建议：保留该边界，展示缺测原因、仍需实现/标定的部分。

- scoring-demo-web/app/scoring/engine.ts:193 if (scenario.mode === "real")
- src/rallymate_scoring/scoring.py:271 if calibration is None:
- src/rallymate_scoring/scoring.py:216 status="unavailable"

### GS-IMPL-08 calibration_required

后端可用标定阈值和单位/版本/方向检查产出等级，但此能力不证明GS已有可信标定；GS requirements缺失。

建议：仅经合法注册、单位一致、教练真值和独立测试后启用，记录threshold_version；不要把文档定性描述伪装数值标定。

- src/rallymate_scoring/scoring.py:475 if backend == "threshold_rule":
- src/rallymate_scoring/scoring.py:486 if item.get("unit") != calibration["unit"]:
- src/rallymate_scoring/scoring.py:495 if calibration["direction"] == "higher_is_better":

### GS-IMPL-09 mapping_required

技术注册表包含相符5种底线技术但每项为五阶段；CARD为10阶段，二者是不同粒度，尚无正式逐阶段等价映射。

建议：名称建立可审阅跨表链接；阶段映射需单列多对多及不确定边界，不能改写原指标编号。

- src/rallymate_scoring/data/technique_metrics.json /techniques
- scoring-demo-web/app/data/technique-catalog.json /techniques

### GS-IMPL-10 source_conflict_preserved

原文异常同样进入两份注册表；一致性通过不等于内容正确。

建议：引入source_review_required状态并绑定冲突ID；源修订前停止相关正式评分或技术纠正断言。

- src/rallymate_scoring/data/metric_cards.json /cards
- scoring-demo-web/app/data/metric-cards.json /cards

## 50 阶段与完整 248 指标

下面逐项完整保留原字段；每项无已定义物理单位、标量优劣方向、技术分数阈值、100分换算、扣分值或权重。计算方式中的加号连接特征族，不代表可以把不同量纲的特征相加得到分数。定性方向见技术定义/关键项，不能强行转换为越大越好。逐字段与代码的 JSON Pointer 对照及 measurement plan 原值见 gs-audit.json。

## GS01 底线正手

51 项；技术注册表映射 baseline_forehand，名称语义相符。十阶段与五阶段不是已证实的一对一关系。

### GS01-M01 盯球准备

[CARD-GS:word/document.xml:P0369] 阶段定义：建立准备姿态，观察来球，与垫步启动

[CARD-GS:word/document.xml:P0370] 开始：球离开对方拍面，    结束：垫步落地，

#### GS01-M01-01 双手持拍

待核验源冲突：GS-CONFLICT-03。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0381; T002.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P0383; T002.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P0385; T002.R007.C002]
- 技术定义：双手持拍。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“盯球准备”及阶段定义理解。 [CARD-GS:word/document.xml:P0387; T002.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0389; T002.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P0391; T002.R010.C002]
- 所需点：J101/J121；J103/J123；J004；J006/J007 [CARD-GS:word/document.xml:P0393; T002.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P0395; T002.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P0397; T002.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P0399; T002.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P0401; T002.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P0403; T002.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P0405; T002.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P0407; T002.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P0409; T002.R019.C002]
- AI正向反馈：双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0411; T002.R020.C002]
- AI改进反馈：双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0413; T002.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M01-02 持续盯球

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0425; T003.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P0427; T003.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P0429; T003.R007.C002]
- 技术定义：持续盯球。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“盯球准备”及阶段定义理解。 [CARD-GS:word/document.xml:P0431; T003.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0433; T003.R009.C002]
- 当前识别点：头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P0435; T003.R010.C002]
- 所需点：J004；J006/J007 [CARD-GS:word/document.xml:P0437; T003.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P0439; T003.R012.C002]
- 计算方式：人体关键点时序变化 + 动作连续性 [CARD-GS:word/document.xml:P0441; T003.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P0443; T003.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P0445; T003.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P0447; T003.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P0449; T003.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P0451; T003.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P0453; T003.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P0455; T003.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P0457; T003.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M01-03 降低重心

待核验源冲突：GS-CONFLICT-03。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0469; T004.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P0471; T004.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P0473; T004.R007.C002]
- 技术定义：降低重心。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“盯球准备”及阶段定义理解。 [CARD-GS:word/document.xml:P0475; T004.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0477; T004.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P0479; T004.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007 [CARD-GS:word/document.xml:P0481; T004.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P0483; T004.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P0485; T004.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P0487; T004.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P0489; T004.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P0491; T004.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P0493; T004.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P0495; T004.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P0497; T004.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P0499; T004.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P0501; T004.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M01-04 完成垫步

待核验源冲突：GS-CONFLICT-03, GS-CONFLICT-04。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0513; T005.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P0515; T005.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P0517; T005.R007.C002]
- 技术定义：完成垫步。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“盯球准备”及阶段定义理解。 [CARD-GS:word/document.xml:P0519; T005.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0521; T005.R009.C002]
- 当前识别点：头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P0523; T005.R010.C002]
- 所需点：J004；J006/J007 [CARD-GS:word/document.xml:P0525; T005.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P0527; T005.R012.C002]
- 计算方式：人体关键点时序变化 + 动作连续性 [CARD-GS:word/document.xml:P0529; T005.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P0531; T005.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P0533; T005.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P0535; T005.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P0537; T005.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P0539; T005.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P0541; T005.R019.C002]
- AI正向反馈：完成垫步完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0543; T005.R020.C002]
- AI改进反馈：完成垫步表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0545; T005.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M01-05 准备启动

待核验源冲突：GS-CONFLICT-03。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0557; T006.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P0559; T006.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P0561; T006.R007.C002]
- 技术定义：准备启动。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“盯球准备”及阶段定义理解。 [CARD-GS:word/document.xml:P0563; T006.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0565; T006.R009.C002]
- 当前识别点：头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P0567; T006.R010.C002]
- 所需点：J004；J006/J007 [CARD-GS:word/document.xml:P0569; T006.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P0571; T006.R012.C002]
- 计算方式：人体关键点时序变化 + 动作连续性 [CARD-GS:word/document.xml:P0573; T006.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P0575; T006.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P0577; T006.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P0579; T006.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P0581; T006.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P0583; T006.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P0585; T006.R019.C002]
- AI正向反馈：准备启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0587; T006.R020.C002]
- AI改进反馈：准备启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0589; T006.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M02 启动移动

[CARD-GS:word/document.xml:P0592] 阶段定义：根据来球方向，完成第一步启动及调整步伐，保持盯球和双手持拍，同时移动到最佳击球位置。

[CARD-GS:word/document.xml:P0593] 开始：左右脚重新接触地面（垫步落地）    结束：开始转体

#### GS01-M02-01 第一步启动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P0604; T007.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P0606; T007.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P0608; T007.R007.C002]
- 技术定义：据来球方向完成第一步启动，身体开始向击球方向移动。 [CARD-GS:word/document.xml:P0610; T007.R008.C002]
- 原文关键项：完成第一启动步；重心开始移动；确定移动方向；上肢保持平衡；身体开始位移 [CARD-GS:word/document.xml:P0612; T007.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P0614; T007.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P0616; T007.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P0618; T007.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P0620; T007.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P0622; T007.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P0624; T007.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P0626; T007.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P0628; T007.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P0630; T007.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P0632; T007.R019.C002]
- AI正向反馈：第一步启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0634; T007.R020.C002]
- AI改进反馈：第一步启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0636; T007.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M02-02 调整步伐

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P0648; T008.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P0650; T008.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P0652; T008.R007.C002]
- 技术定义：根据来球方向调整步伐及移动距离。 [CARD-GS:word/document.xml:P0654; T008.R008.C002]
- 原文关键项：左右脚协调移动；调整步幅；控制移动方向；控制移动距离；保持身体上肢稳定 [CARD-GS:word/document.xml:P0656; T008.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P0658; T008.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P0660; T008.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P0662; T008.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P0664; T008.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P0666; T008.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P0668; T008.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P0670; T008.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P0672; T008.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P0674; T008.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P0676; T008.R019.C002]
- AI正向反馈：调整步伐完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0678; T008.R020.C002]
- AI改进反馈：调整步伐表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0680; T008.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M02-03 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P0692; T009.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P0694; T009.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P0696; T009.R007.C002]
- 技术定义：移动过程中持续观察来球位置、速度及飞行方向。 [CARD-GS:word/document.xml:P0698; T009.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P0700; T009.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P0702; T009.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P0704; T009.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P0706; T009.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P0708; T009.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P0710; T009.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P0712; T009.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P0714; T009.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P0716; T009.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P0718; T009.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P0720; T009.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P0722; T009.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P0724; T009.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M02-04 身体移动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P0736; T010.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P0738; T010.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P0740; T010.R007.C002]
- 技术定义：身体整体随步伐向来球方向移动。 [CARD-GS:word/document.xml:P0742; T010.R008.C002]
- 原文关键项：重心持续移动；身体整体移动；保持动态平衡；控制移动节奏；保持身体稳定 [CARD-GS:word/document.xml:P0744; T010.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P0746; T010.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P0748; T010.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P0750; T010.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P0752; T010.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P0754; T010.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P0756; T010.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P0758; T010.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P0760; T010.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P0762; T010.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P0764; T010.R019.C002]
- AI正向反馈：身体移动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0766; T010.R020.C002]
- AI改进反馈：身体移动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0768; T010.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M02-05 保持双手持拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P0780; T011.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P0782; T011.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P0784; T011.R007.C002]
- 技术定义：移动过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P0786; T011.R008.C002]
- 原文关键项：双手保持握拍；球拍保持身体前方；拍面保持稳定；双手共同控制球拍；保持准备姿态 [CARD-GS:word/document.xml:P0788; T011.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P0790; T011.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P0792; T011.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P0794; T011.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P0796; T011.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P0798; T011.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P0800; T011.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P0802; T011.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P0804; T011.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P0806; T011.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P0808; T011.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0810; T011.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0812; T011.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M03 转肩引拍

[CARD-GS:word/document.xml:P0815] 阶段定义：完成单位转体，非持拍手辅助引拍，球拍整体后摆，同时将身体重心逐渐加载到持拍手同侧支撑腿，为M04主动蹬地储存势能。

[CARD-GS:word/document.xml:P0816] 开始：开始完成单位转体，肩线开始旋转，非持拍手辅助球拍整体转动，    结束：重心成功加载至持拍手同侧支撑腿，重心完成侧向转移，支撑腿承重稳定，为主动蹬地做准备

#### GS01-M03-01 完成单位转体

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0827; T012.R005.C002]
- 开始标志性动作：开始完成单位转体，肩线开始旋转，非持拍手辅助球拍整体转动， [CARD-GS:word/document.xml:P0829; T012.R006.C002]
- 结束标志性动作：重心成功加载至持拍手同侧支撑腿，重心完成侧向转移，支撑腿承重稳定，为主动蹬地做准备 [CARD-GS:word/document.xml:P0831; T012.R007.C002]
- 技术定义：完成单位转体。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转肩引拍”及阶段定义理解。 [CARD-GS:word/document.xml:P0833; T012.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0835; T012.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序 [CARD-GS:word/document.xml:P0837; T012.R010.C002]
- 所需点：J033/J034；J071/J072 [CARD-GS:word/document.xml:P0839; T012.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P0841; T012.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 [CARD-GS:word/document.xml:P0843; T012.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P0845; T012.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P0847; T012.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P0849; T012.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P0851; T012.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P0853; T012.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P0855; T012.R019.C002]
- AI正向反馈：身体转动较完整，肩部旋转连续。 [CARD-GS:word/document.xml:P0857; T012.R020.C002]
- AI改进反馈：身体转动不足或衔接偏慢，建议减少只用手臂引拍的情况。 [CARD-GS:word/document.xml:P0859; T012.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M03-02 非持拍手辅助

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0871; T013.R005.C002]
- 开始标志性动作：开始完成单位转体，肩线开始旋转，非持拍手辅助球拍整体转动， [CARD-GS:word/document.xml:P0873; T013.R006.C002]
- 结束标志性动作：重心成功加载至持拍手同侧支撑腿，重心完成侧向转移，支撑腿承重稳定，为主动蹬地做准备 [CARD-GS:word/document.xml:P0875; T013.R007.C002]
- 技术定义：非持拍手辅助。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转肩引拍”及阶段定义理解。 [CARD-GS:word/document.xml:P0877; T013.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0879; T013.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性 [CARD-GS:word/document.xml:P0881; T013.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123 [CARD-GS:word/document.xml:P0883; T013.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P0885; T013.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P0887; T013.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P0889; T013.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P0891; T013.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P0893; T013.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P0895; T013.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P0897; T013.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P0899; T013.R019.C002]
- AI正向反馈：非持拍手辅助完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P0901; T013.R020.C002]
- AI改进反馈：非持拍手辅助表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P0903; T013.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M03-03 球拍整体后摆

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0915; T014.R005.C002]
- 开始标志性动作：开始完成单位转体，肩线开始旋转，非持拍手辅助球拍整体转动， [CARD-GS:word/document.xml:P0917; T014.R006.C002]
- 结束标志性动作：重心成功加载至持拍手同侧支撑腿，重心完成侧向转移，支撑腿承重稳定，为主动蹬地做准备 [CARD-GS:word/document.xml:P0919; T014.R007.C002]
- 技术定义：球拍整体后摆。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转肩引拍”及阶段定义理解。 [CARD-GS:word/document.xml:P0921; T014.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0923; T014.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P0925; T014.R010.C002]
- 所需点：J033/J034；J071/J072；RK专项关键点（训练后） [CARD-GS:word/document.xml:P0927; T014.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P0929; T014.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P0931; T014.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P0933; T014.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P0935; T014.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P0937; T014.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P0939; T014.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P0941; T014.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P0943; T014.R019.C002]
- AI正向反馈：球拍整体后摆的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P0945; T014.R020.C002]
- AI改进反馈：球拍整体后摆存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P0947; T014.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M03-04 重心加载至持拍侧

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P0959; T015.R005.C002]
- 开始标志性动作：开始完成单位转体，肩线开始旋转，非持拍手辅助球拍整体转动， [CARD-GS:word/document.xml:P0961; T015.R006.C002]
- 结束标志性动作：重心成功加载至持拍手同侧支撑腿，重心完成侧向转移，支撑腿承重稳定，为主动蹬地做准备 [CARD-GS:word/document.xml:P0963; T015.R007.C002]
- 技术定义：重心加载至持拍侧。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转肩引拍”及阶段定义理解。 [CARD-GS:word/document.xml:P0965; T015.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P0967; T015.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P0969; T015.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P0971; T015.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P0973; T015.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P0975; T015.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P0977; T015.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P0979; T015.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P0981; T015.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P0983; T015.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P0985; T015.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P0987; T015.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P0989; T015.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P0991; T015.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M03-05 保持动态平衡

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1003; T016.R005.C002]
- 开始标志性动作：开始完成单位转体，肩线开始旋转，非持拍手辅助球拍整体转动， [CARD-GS:word/document.xml:P1005; T016.R006.C002]
- 结束标志性动作：重心成功加载至持拍手同侧支撑腿，重心完成侧向转移，支撑腿承重稳定，为主动蹬地做准备 [CARD-GS:word/document.xml:P1007; T016.R007.C002]
- 技术定义：保持动态平衡。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转肩引拍”及阶段定义理解。 [CARD-GS:word/document.xml:P1009; T016.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1011; T016.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1013; T016.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1015; T016.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1017; T016.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1019; T016.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P1021; T016.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P1023; T016.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P1025; T016.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P1027; T016.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P1029; T016.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1031; T016.R019.C002]
- AI正向反馈：保持动态平衡完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1033; T016.R020.C002]
- AI改进反馈：保持动态平衡表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1035; T016.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M04 主动蹬地

[CARD-GS:word/document.xml:P1038] 阶段定义：利用持拍手同侧支撑腿主动蹬地产生地面反作用力，推动身体重心向前上方运动，为骨盆主动旋转提供动力来源。

[CARD-GS:word/document.xml:P1039] 开始：重心成功加载至持拍手同侧支撑腿，支撑腿承重稳定，身体完成蓄力姿态|J：J070 J077 J146 J166；S：S013；K：K050 K060 K080    结束：骨盆开始主动旋转，骨盆角速度明显增加，动力链开始向前上方传递

#### GS01-M04-01 主动蹬地

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1050; T017.R005.C002]
- 开始标志性动作：重心成功加载至持拍手同侧支撑腿，支撑腿承重稳定，身体完成蓄力姿态|J：J070 J077 J146 J166；S：S013；K：K050 K060 K080 [CARD-GS:word/document.xml:P1052; T017.R006.C002]
- 结束标志性动作：骨盆开始主动旋转，骨盆角速度明显增加，动力链开始向前上方传递 [CARD-GS:word/document.xml:P1054; T017.R007.C002]
- 技术定义：主动蹬地。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“主动蹬地”及阶段定义理解。 [CARD-GS:word/document.xml:P1056; T017.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1058; T017.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1060; T017.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1062; T017.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1064; T017.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1066; T017.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P1068; T017.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P1070; T017.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P1072; T017.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P1074; T017.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P1076; T017.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1078; T017.R019.C002]
- AI正向反馈：主动蹬地完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1080; T017.R020.C002]
- AI改进反馈：主动蹬地表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1082; T017.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M04-02 重心向前上转移

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1094; T018.R005.C002]
- 开始标志性动作：重心成功加载至持拍手同侧支撑腿，支撑腿承重稳定，身体完成蓄力姿态|J：J070 J077 J146 J166；S：S013；K：K050 K060 K080 [CARD-GS:word/document.xml:P1096; T018.R006.C002]
- 结束标志性动作：骨盆开始主动旋转，骨盆角速度明显增加，动力链开始向前上方传递 [CARD-GS:word/document.xml:P1098; T018.R007.C002]
- 技术定义：重心向前上转移。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“主动蹬地”及阶段定义理解。 [CARD-GS:word/document.xml:P1100; T018.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1102; T018.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1104; T018.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1106; T018.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1108; T018.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1110; T018.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1112; T018.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1114; T018.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1116; T018.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1118; T018.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1120; T018.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1122; T018.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P1124; T018.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P1126; T018.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M04-03 稳定支撑（重心没有跳动，保持身体不变形）

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1138; T019.R005.C002]
- 开始标志性动作：重心成功加载至持拍手同侧支撑腿，支撑腿承重稳定，身体完成蓄力姿态|J：J070 J077 J146 J166；S：S013；K：K050 K060 K080 [CARD-GS:word/document.xml:P1140; T019.R006.C002]
- 结束标志性动作：骨盆开始主动旋转，骨盆角速度明显增加，动力链开始向前上方传递 [CARD-GS:word/document.xml:P1142; T019.R007.C002]
- 技术定义：稳定支撑（重心没有跳动，保持身体不变形）。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“主动蹬地”及阶段定义理解。 [CARD-GS:word/document.xml:P1144; T019.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1146; T019.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1148; T019.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1150; T019.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1152; T019.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1154; T019.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P1156; T019.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P1158; T019.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P1160; T019.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P1162; T019.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P1164; T019.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1166; T019.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P1168; T019.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P1170; T019.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M04-04 建立地面反作用力

待核验源冲突：GS-CONFLICT-06。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1182; T020.R005.C002]
- 开始标志性动作：重心成功加载至持拍手同侧支撑腿，支撑腿承重稳定，身体完成蓄力姿态|J：J070 J077 J146 J166；S：S013；K：K050 K060 K080 [CARD-GS:word/document.xml:P1184; T020.R006.C002]
- 结束标志性动作：骨盆开始主动旋转，骨盆角速度明显增加，动力链开始向前上方传递 [CARD-GS:word/document.xml:P1186; T020.R007.C002]
- 技术定义：建立地面反作用力。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“主动蹬地”及阶段定义理解。 [CARD-GS:word/document.xml:P1188; T020.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1190; T020.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1192; T020.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1194; T020.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P1196; T020.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1198; T020.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P1200; T020.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P1202; T020.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P1204; T020.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P1206; T020.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P1208; T020.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P1210; T020.R019.C002]
- AI正向反馈：建立地面反作用力完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1212; T020.R020.C002]
- AI改进反馈：建立地面反作用力表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1214; T020.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M04-05 非持拍手主动释放并保持身体平衡

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1226; T021.R005.C002]
- 开始标志性动作：重心成功加载至持拍手同侧支撑腿，支撑腿承重稳定，身体完成蓄力姿态|J：J070 J077 J146 J166；S：S013；K：K050 K060 K080 [CARD-GS:word/document.xml:P1228; T021.R006.C002]
- 结束标志性动作：骨盆开始主动旋转，骨盆角速度明显增加，动力链开始向前上方传递 [CARD-GS:word/document.xml:P1230; T021.R007.C002]
- 技术定义：非持拍手主动释放并保持身体平衡。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“主动蹬地”及阶段定义理解。 [CARD-GS:word/document.xml:P1232; T021.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1234; T021.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1236; T021.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1238; T021.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1240; T021.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1242; T021.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P1244; T021.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P1246; T021.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P1248; T021.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P1250; T021.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P1252; T021.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1254; T021.R019.C002]
- AI正向反馈：非持拍手主动释放并保持身体平衡完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1256; T021.R020.C002]
- AI改进反馈：非持拍手主动释放并保持身体平衡表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1258; T021.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M04-06 准备骨盆启动

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1270; T022.R005.C002]
- 开始标志性动作：重心成功加载至持拍手同侧支撑腿，支撑腿承重稳定，身体完成蓄力姿态|J：J070 J077 J146 J166；S：S013；K：K050 K060 K080 [CARD-GS:word/document.xml:P1272; T022.R006.C002]
- 结束标志性动作：骨盆开始主动旋转，骨盆角速度明显增加，动力链开始向前上方传递 [CARD-GS:word/document.xml:P1274; T022.R007.C002]
- 技术定义：准备骨盆启动。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“主动蹬地”及阶段定义理解。 [CARD-GS:word/document.xml:P1276; T022.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1278; T022.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1280; T022.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1282; T022.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1284; T022.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1286; T022.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P1288; T022.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P1290; T022.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P1292; T022.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P1294; T022.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P1296; T022.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1298; T022.R019.C002]
- AI正向反馈：准备骨盆启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1300; T022.R020.C002]
- AI改进反馈：准备骨盆启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1302; T022.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M05 转髋

[CARD-GS:word/document.xml:P1305] 阶段定义：骨盆主动旋转，利用下肢产生的动力驱动身体旋转，将地面反作用力向上肢传递，为胸廓旋转及手臂加速建立动力链。

[CARD-GS:word/document.xml:P1306] 开始：支撑腿主动蹬地完成，骨盆开始主动旋转,动力链开始向前上方传递| J：J070 J071 J072；K：K080 K081    结束：肩髋分离达到最大值，胸廓开始主动旋转，肩髋分离角达到峰值，胸廓开始接管动力链

#### GS01-M05-01 骨盆主动旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1317; T023.R005.C002]
- 开始标志性动作：支撑腿主动蹬地完成，骨盆开始主动旋转,动力链开始向前上方传递| J：J070 J071 J072；K：K080 K081 [CARD-GS:word/document.xml:P1319; T023.R006.C002]
- 结束标志性动作：肩髋分离达到最大值，胸廓开始主动旋转，肩髋分离角达到峰值，胸廓开始接管动力链 [CARD-GS:word/document.xml:P1321; T023.R007.C002]
- 技术定义：骨盆主动旋转。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转髋”及阶段定义理解。 [CARD-GS:word/document.xml:P1323; T023.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1325; T023.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P1327; T023.R010.C002]
- 所需点：J033/J034；J071/J072；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P1329; T023.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1331; T023.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P1333; T023.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1335; T023.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1337; T023.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1339; T023.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1341; T023.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1343; T023.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P1345; T023.R019.C002]
- AI正向反馈：骨盆主动旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1347; T023.R020.C002]
- AI改进反馈：骨盆主动旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1349; T023.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M05-02 重心持续前移

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1361; T024.R005.C002]
- 开始标志性动作：支撑腿主动蹬地完成，骨盆开始主动旋转,动力链开始向前上方传递| J：J070 J071 J072；K：K080 K081 [CARD-GS:word/document.xml:P1363; T024.R006.C002]
- 结束标志性动作：肩髋分离达到最大值，胸廓开始主动旋转，肩髋分离角达到峰值，胸廓开始接管动力链 [CARD-GS:word/document.xml:P1365; T024.R007.C002]
- 技术定义：重心持续前移。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转髋”及阶段定义理解。 [CARD-GS:word/document.xml:P1367; T024.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1369; T024.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1371; T024.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1373; T024.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1375; T024.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1377; T024.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1379; T024.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1381; T024.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1383; T024.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1385; T024.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1387; T024.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1389; T024.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P1391; T024.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P1393; T024.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M05-03 下肢动力向上传递

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1405; T025.R005.C002]
- 开始标志性动作：支撑腿主动蹬地完成，骨盆开始主动旋转,动力链开始向前上方传递| J：J070 J071 J072；K：K080 K081 [CARD-GS:word/document.xml:P1407; T025.R006.C002]
- 结束标志性动作：肩髋分离达到最大值，胸廓开始主动旋转，肩髋分离角达到峰值，胸廓开始接管动力链 [CARD-GS:word/document.xml:P1409; T025.R007.C002]
- 技术定义：下肢动力向上传递。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转髋”及阶段定义理解。 [CARD-GS:word/document.xml:P1411; T025.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1413; T025.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1415; T025.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1417; T025.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1419; T025.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1421; T025.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1423; T025.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1425; T025.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1427; T025.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1429; T025.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1431; T025.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1433; T025.R019.C002]
- AI正向反馈：下肢动力向上传递完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1435; T025.R020.C002]
- AI改进反馈：下肢动力向上传递表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1437; T025.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M05-04 建立肩髋分离（保持盯球）

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1449; T026.R005.C002]
- 开始标志性动作：支撑腿主动蹬地完成，骨盆开始主动旋转,动力链开始向前上方传递| J：J070 J071 J072；K：K080 K081 [CARD-GS:word/document.xml:P1451; T026.R006.C002]
- 结束标志性动作：肩髋分离达到最大值，胸廓开始主动旋转，肩髋分离角达到峰值，胸廓开始接管动力链 [CARD-GS:word/document.xml:P1453; T026.R007.C002]
- 技术定义：建立肩髋分离（保持盯球）。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转髋”及阶段定义理解。 [CARD-GS:word/document.xml:P1455; T026.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1457; T026.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P1459; T026.R010.C002]
- 所需点：J033/J034；J071/J072；J004；J006/J007 [CARD-GS:word/document.xml:P1461; T026.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P1463; T026.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 [CARD-GS:word/document.xml:P1465; T026.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1467; T026.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1469; T026.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1471; T026.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1473; T026.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1475; T026.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P1477; T026.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P1479; T026.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P1481; T026.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M05-05 非持拍手主动配平并维持肩髋分离

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1493; T027.R005.C002]
- 开始标志性动作：支撑腿主动蹬地完成，骨盆开始主动旋转,动力链开始向前上方传递| J：J070 J071 J072；K：K080 K081 [CARD-GS:word/document.xml:P1495; T027.R006.C002]
- 结束标志性动作：肩髋分离达到最大值，胸廓开始主动旋转，肩髋分离角达到峰值，胸廓开始接管动力链 [CARD-GS:word/document.xml:P1497; T027.R007.C002]
- 技术定义：非持拍手主动配平并维持肩髋分离。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转髋”及阶段定义理解。 [CARD-GS:word/document.xml:P1499; T027.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1501; T027.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性 [CARD-GS:word/document.xml:P1503; T027.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123 [CARD-GS:word/document.xml:P1505; T027.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1507; T027.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P1509; T027.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1511; T027.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1513; T027.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1515; T027.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1517; T027.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1519; T027.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1521; T027.R019.C002]
- AI正向反馈：非持拍手主动配平并维持肩髋分离完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1523; T027.R020.C002]
- AI改进反馈：非持拍手主动配平并维持肩髋分离表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1525; T027.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M05-06 准备胸廓启动

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1537; T028.R005.C002]
- 开始标志性动作：支撑腿主动蹬地完成，骨盆开始主动旋转,动力链开始向前上方传递| J：J070 J071 J072；K：K080 K081 [CARD-GS:word/document.xml:P1539; T028.R006.C002]
- 结束标志性动作：肩髋分离达到最大值，胸廓开始主动旋转，肩髋分离角达到峰值，胸廓开始接管动力链 [CARD-GS:word/document.xml:P1541; T028.R007.C002]
- 技术定义：准备胸廓启动。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转髋”及阶段定义理解。 [CARD-GS:word/document.xml:P1543; T028.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1545; T028.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序 [CARD-GS:word/document.xml:P1547; T028.R010.C002]
- 所需点：J033/J034；J071/J072 [CARD-GS:word/document.xml:P1549; T028.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1551; T028.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 [CARD-GS:word/document.xml:P1553; T028.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1555; T028.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1557; T028.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1559; T028.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1561; T028.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1563; T028.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1565; T028.R019.C002]
- AI正向反馈：准备胸廓启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1567; T028.R020.C002]
- AI改进反馈：准备胸廓启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1569; T028.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M06 转胸

[CARD-GS:word/document.xml:P1572] 阶段定义：胸廓主动旋转，利用骨盆产生的动力驱动上半身运动，进一步放大动力链，为手臂加速和球拍释放创造条件。

[CARD-GS:word/document.xml:P1573] 开始：肩髋分离达到最大值，胸廓开始主动旋转    结束：持拍手开始主动加速，球拍进入释放准备状态

#### GS01-M06-01 胸廓主动旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1584; T029.R005.C002]
- 开始标志性动作：肩髋分离达到最大值，胸廓开始主动旋转 [CARD-GS:word/document.xml:P1586; T029.R006.C002]
- 结束标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1588; T029.R007.C002]
- 技术定义：胸廓主动旋转。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转胸”及阶段定义理解。 [CARD-GS:word/document.xml:P1590; T029.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1592; T029.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P1594; T029.R010.C002]
- 所需点：J033/J034；J071/J072；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P1596; T029.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1598; T029.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P1600; T029.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1602; T029.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1604; T029.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1606; T029.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1608; T029.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1610; T029.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P1612; T029.R019.C002]
- AI正向反馈：胸廓主动旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1614; T029.R020.C002]
- AI改进反馈：胸廓主动旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1616; T029.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M06-02 肩髋分离逐渐释放，肩开始追赶骨盆（角度）

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1628; T030.R005.C002]
- 开始标志性动作：肩髋分离达到最大值，胸廓开始主动旋转 [CARD-GS:word/document.xml:P1630; T030.R006.C002]
- 结束标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1632; T030.R007.C002]
- 技术定义：肩髋分离逐渐释放，肩开始追赶骨盆（角度）。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转胸”及阶段定义理解。 [CARD-GS:word/document.xml:P1634; T030.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1636; T030.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序 [CARD-GS:word/document.xml:P1638; T030.R010.C002]
- 所需点：J033/J034；J071/J072 [CARD-GS:word/document.xml:P1640; T030.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1642; T030.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 [CARD-GS:word/document.xml:P1644; T030.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1646; T030.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1648; T030.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1650; T030.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1652; T030.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1654; T030.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1656; T030.R019.C002]
- AI正向反馈：肩髋分离逐渐释放，肩开始追赶骨盆（角度）完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1658; T030.R020.C002]
- AI改进反馈：肩髋分离逐渐释放，肩开始追赶骨盆（角度）表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1660; T030.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M06-03 持拍手进入准备加速姿态

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1672; T031.R005.C002]
- 开始标志性动作：肩髋分离达到最大值，胸廓开始主动旋转 [CARD-GS:word/document.xml:P1674; T031.R006.C002]
- 结束标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1676; T031.R007.C002]
- 技术定义：持拍手进入准备加速姿态。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转胸”及阶段定义理解。 [CARD-GS:word/document.xml:P1678; T031.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1680; T031.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性 [CARD-GS:word/document.xml:P1682; T031.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123 [CARD-GS:word/document.xml:P1684; T031.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1686; T031.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P1688; T031.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1690; T031.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1692; T031.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1694; T031.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1696; T031.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1698; T031.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1700; T031.R019.C002]
- AI正向反馈：持拍手进入准备加速姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1702; T031.R020.C002]
- AI改进反馈：持拍手进入准备加速姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1704; T031.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M06-04 非持拍手主动配合胸廓旋转并维持动态平衡

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1716; T032.R005.C002]
- 开始标志性动作：肩髋分离达到最大值，胸廓开始主动旋转 [CARD-GS:word/document.xml:P1718; T032.R006.C002]
- 结束标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1720; T032.R007.C002]
- 技术定义：非持拍手主动配合胸廓旋转并维持动态平衡。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转胸”及阶段定义理解。 [CARD-GS:word/document.xml:P1722; T032.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1724; T032.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P1726; T032.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P1728; T032.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1730; T032.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P1732; T032.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P1734; T032.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P1736; T032.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P1738; T032.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P1740; T032.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P1742; T032.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P1744; T032.R019.C002]
- AI正向反馈：非持拍手主动配合胸廓旋转并维持动态平衡完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1746; T032.R020.C002]
- AI改进反馈：非持拍手主动配合胸廓旋转并维持动态平衡表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1748; T032.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M06-05 准备球拍释放

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1760; T033.R005.C002]
- 开始标志性动作：肩髋分离达到最大值，胸廓开始主动旋转 [CARD-GS:word/document.xml:P1762; T033.R006.C002]
- 结束标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1764; T033.R007.C002]
- 技术定义：准备球拍释放。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“转胸”及阶段定义理解。 [CARD-GS:word/document.xml:P1766; T033.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1768; T033.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P1770; T033.R010.C002]
- 所需点：J033/J034；J071/J072；RK专项关键点（训练后） [CARD-GS:word/document.xml:P1772; T033.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1774; T033.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P1776; T033.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1778; T033.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1780; T033.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1782; T033.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1784; T033.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1786; T033.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P1788; T033.R019.C002]
- AI正向反馈：准备球拍释放的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P1790; T033.R020.C002]
- AI改进反馈：准备球拍释放存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P1792; T033.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M07 手臂加速与挥拍

[CARD-GS:word/document.xml:P1795] 阶段定义： 持拍手主动加速，球拍滞后达到最大并逐渐释放，球拍轨迹逐渐向预测击球点收敛，为击球创造最佳条件

[CARD-GS:word/document.xml:P1796] 开始：持拍手开始主动加速，球拍进入释放准备状态    结束：球拍轨迹与预测击球轨迹开始收敛

#### GS01-M07-01 持拍手主动加速

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1807; T034.R005.C002]
- 开始标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1809; T034.R006.C002]
- 结束标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P1811; T034.R007.C002]
- 技术定义：持拍手主动加速。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“手臂加速与挥拍”及阶段定义理解。 [CARD-GS:word/document.xml:P1813; T034.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1815; T034.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性 [CARD-GS:word/document.xml:P1817; T034.R010.C002]
- 所需点：J101/J121；J103/J123 [CARD-GS:word/document.xml:P1819; T034.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1821; T034.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P1823; T034.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1825; T034.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1827; T034.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1829; T034.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1831; T034.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1833; T034.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1835; T034.R019.C002]
- AI正向反馈：持拍手主动加速完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1837; T034.R020.C002]
- AI改进反馈：持拍手主动加速表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1839; T034.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M07-02 球拍滞后达到最大

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1851; T035.R005.C002]
- 开始标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1853; T035.R006.C002]
- 结束标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P1855; T035.R007.C002]
- 技术定义：球拍滞后达到最大。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“手臂加速与挥拍”及阶段定义理解。 [CARD-GS:word/document.xml:P1857; T035.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1859; T035.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P1861; T035.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P1863; T035.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1865; T035.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P1867; T035.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1869; T035.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1871; T035.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1873; T035.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1875; T035.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1877; T035.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P1879; T035.R019.C002]
- AI正向反馈：球拍滞后达到最大的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P1881; T035.R020.C002]
- AI改进反馈：球拍滞后达到最大存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P1883; T035.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M07-03 拍头主动释放

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1895; T036.R005.C002]
- 开始标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1897; T036.R006.C002]
- 结束标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P1899; T036.R007.C002]
- 技术定义：拍头主动释放。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“手臂加速与挥拍”及阶段定义理解。 [CARD-GS:word/document.xml:P1901; T036.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1903; T036.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P1905; T036.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P1907; T036.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P1909; T036.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P1911; T036.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P1913; T036.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P1915; T036.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P1917; T036.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P1919; T036.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P1921; T036.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P1923; T036.R019.C002]
- AI正向反馈：拍头主动释放的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P1925; T036.R020.C002]
- AI改进反馈：拍头主动释放存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P1927; T036.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M07-04 非持拍手维持身体动态稳定

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1939; T037.R005.C002]
- 开始标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1941; T037.R006.C002]
- 结束标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P1943; T037.R007.C002]
- 技术定义：非持拍手维持身体动态稳定。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“手臂加速与挥拍”及阶段定义理解。 [CARD-GS:word/document.xml:P1945; T037.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1947; T037.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P1949; T037.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P1951; T037.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P1953; T037.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P1955; T037.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P1957; T037.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P1959; T037.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P1961; T037.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P1963; T037.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P1965; T037.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P1967; T037.R019.C002]
- AI正向反馈：非持拍手维持身体动态稳定完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P1969; T037.R020.C002]
- AI改进反馈：非持拍手维持身体动态稳定表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P1971; T037.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M07-05 球拍轨迹逐渐向预测击球点收敛

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P1983; T038.R005.C002]
- 开始标志性动作：持拍手开始主动加速，球拍进入释放准备状态 [CARD-GS:word/document.xml:P1985; T038.R006.C002]
- 结束标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P1987; T038.R007.C002]
- 技术定义：球拍轨迹逐渐向预测击球点收敛。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“手臂加速与挥拍”及阶段定义理解。 [CARD-GS:word/document.xml:P1989; T038.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P1991; T038.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P1993; T038.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P1995; T038.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P1997; T038.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P1999; T038.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P2001; T038.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P2003; T038.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P2005; T038.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P2007; T038.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P2009; T038.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2011; T038.R019.C002]
- AI正向反馈：球拍轨迹逐渐向预测击球点收敛的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P2013; T038.R020.C002]
- AI改进反馈：球拍轨迹逐渐向预测击球点收敛存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P2015; T038.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M08 击球

[CARD-GS:word/document.xml:P2018] 阶段定义：球拍与球发生接触，完成能量传递，控制击球方向、速度及旋转，并建立击球后的运动状态。

[CARD-GS:word/document.xml:P2019] 开始：球拍轨迹与预测击球轨迹开始收敛    结束：球离开拍面

#### GS01-M08-01 保持稳定击球姿态

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2030; T039.R005.C002]
- 开始标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P2032; T039.R006.C002]
- 结束标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2034; T039.R007.C002]
- 技术定义：保持稳定击球姿态。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“击球”及阶段定义理解。 [CARD-GS:word/document.xml:P2036; T039.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2038; T039.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P2040; T039.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P2042; T039.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2044; T039.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P2046; T039.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2048; T039.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2050; T039.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2052; T039.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2054; T039.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2056; T039.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P2058; T039.R019.C002]
- AI正向反馈：保持稳定击球姿态在活动球与球拍专项事件可用后可进行正式评价。 [CARD-GS:word/document.xml:P2060; T039.R020.C002]
- AI改进反馈：保持稳定击球姿态当前不应由AI直接猜测；需等待稳定球轨迹、球拍关键点和触球事件后再给技术结论。 [CARD-GS:word/document.xml:P2062; T039.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M08-02 球拍到达最佳击球区域

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2074; T040.R005.C002]
- 开始标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P2076; T040.R006.C002]
- 结束标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2078; T040.R007.C002]
- 技术定义：球拍到达最佳击球区域。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“击球”及阶段定义理解。 [CARD-GS:word/document.xml:P2080; T040.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2082; T040.R009.C002]
- 当前识别点：当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P2084; T040.R010.C002]
- 所需点：RK专项关键点（训练后） [CARD-GS:word/document.xml:P2086; T040.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P2088; T040.R012.C002]
- 计算方式：球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P2090; T040.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P2092; T040.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P2094; T040.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P2096; T040.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P2098; T040.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P2100; T040.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P2102; T040.R019.C002]
- AI正向反馈：球拍到达最佳击球区域的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P2104; T040.R020.C002]
- AI改进反馈：球拍到达最佳击球区域存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P2106; T040.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M08-03 球拍与球接触

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2118; T041.R005.C002]
- 开始标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P2120; T041.R006.C002]
- 结束标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2122; T041.R007.C002]
- 技术定义：球拍与球接触。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“击球”及阶段定义理解。 [CARD-GS:word/document.xml:P2124; T041.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2126; T041.R009.C002]
- 当前识别点：当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2128; T041.R010.C002]
- 所需点：RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2130; T041.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P2132; T041.R012.C002]
- 计算方式：球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2134; T041.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P2136; T041.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P2138; T041.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P2140; T041.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P2142; T041.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P2144; T041.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2146; T041.R019.C002]
- AI正向反馈：球拍与球接触的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P2148; T041.R020.C002]
- AI改进反馈：球拍与球接触存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P2150; T041.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M08-04 完成能量传递

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2162; T042.R005.C002]
- 开始标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P2164; T042.R006.C002]
- 结束标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2166; T042.R007.C002]
- 技术定义：完成能量传递。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“击球”及阶段定义理解。 [CARD-GS:word/document.xml:P2168; T042.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2170; T042.R009.C002]
- 当前识别点：人体关键点相对位置、速度、持续时间与动作连续性 [CARD-GS:word/document.xml:P2172; T042.R010.C002]
- 所需点：COCO-17目标球员骨架 [CARD-GS:word/document.xml:P2174; T042.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P2176; T042.R012.C002]
- 计算方式：人体关键点时序变化 + 动作连续性 [CARD-GS:word/document.xml:P2178; T042.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P2180; T042.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P2182; T042.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P2184; T042.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P2186; T042.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P2188; T042.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P2190; T042.R019.C002]
- AI正向反馈：完成能量传递完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2192; T042.R020.C002]
- AI改进反馈：完成能量传递表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2194; T042.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M08-05 球离开拍面

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2206; T043.R005.C002]
- 开始标志性动作：球拍轨迹与预测击球轨迹开始收敛 [CARD-GS:word/document.xml:P2208; T043.R006.C002]
- 结束标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2210; T043.R007.C002]
- 技术定义：球离开拍面。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“击球”及阶段定义理解。 [CARD-GS:word/document.xml:P2212; T043.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2214; T043.R009.C002]
- 当前识别点：当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2216; T043.R010.C002]
- 所需点：RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2218; T043.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P2220; T043.R012.C002]
- 计算方式：球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2222; T043.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P2224; T043.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P2226; T043.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P2228; T043.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P2230; T043.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P2232; T043.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2234; T043.R019.C002]
- AI正向反馈：球离开拍面的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P2236; T043.R020.C002]
- AI改进反馈：球离开拍面存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P2238; T043.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M09 收拍

[CARD-GS:word/document.xml:P2241] 阶段定义：击球后身体继续完成旋转及挥拍动作，重心持续转移，球拍继续挥动，非持拍手主动接回球拍，完成收拍动作，为恢复创造条件。

[CARD-GS:word/document.xml:P2242] 开始：球离开拍面    结束：非持拍手重新接触球拍

#### GS01-M09-01 身体继续旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2253; T044.R005.C002]
- 开始标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2255; T044.R006.C002]
- 结束标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2257; T044.R007.C002]
- 技术定义：身体继续旋转。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“收拍”及阶段定义理解。 [CARD-GS:word/document.xml:P2259; T044.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2261; T044.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2263; T044.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2265; T044.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2267; T044.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2269; T044.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P2271; T044.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P2273; T044.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P2275; T044.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P2277; T044.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P2279; T044.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2281; T044.R019.C002]
- AI正向反馈：身体继续旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2283; T044.R020.C002]
- AI改进反馈：身体继续旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2285; T044.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M09-02 重心持续移动

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2297; T045.R005.C002]
- 开始标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2299; T045.R006.C002]
- 结束标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2301; T045.R007.C002]
- 技术定义：重心持续移动。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“收拍”及阶段定义理解。 [CARD-GS:word/document.xml:P2303; T045.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2305; T045.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P2307; T045.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P2309; T045.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2311; T045.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P2313; T045.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P2315; T045.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P2317; T045.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P2319; T045.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P2321; T045.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P2323; T045.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P2325; T045.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P2327; T045.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P2329; T045.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M09-03 球拍继续挥动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2341; T046.R005.C002]
- 开始标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2343; T046.R006.C002]
- 结束标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2345; T046.R007.C002]
- 技术定义：球拍继续挥动。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“收拍”及阶段定义理解。 [CARD-GS:word/document.xml:P2347; T046.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2349; T046.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P2351; T046.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P2353; T046.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2355; T046.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P2357; T046.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P2359; T046.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P2361; T046.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P2363; T046.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P2365; T046.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P2367; T046.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P2369; T046.R019.C002]
- AI正向反馈：球拍继续挥动的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P2371; T046.R020.C002]
- AI改进反馈：球拍继续挥动存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P2373; T046.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M09-04 非持拍手主动接近球拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2385; T047.R005.C002]
- 开始标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2387; T047.R006.C002]
- 结束标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2389; T047.R007.C002]
- 技术定义：非持拍手主动接近球拍。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“收拍”及阶段定义理解。 [CARD-GS:word/document.xml:P2391; T047.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2393; T047.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P2395; T047.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P2397; T047.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2399; T047.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P2401; T047.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P2403; T047.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P2405; T047.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P2407; T047.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P2409; T047.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P2411; T047.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P2413; T047.R019.C002]
- AI正向反馈：非持拍手主动接近球拍的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P2415; T047.R020.C002]
- AI改进反馈：非持拍手主动接近球拍存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P2417; T047.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M09-05 球拍重新进入非持拍手控制

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2429; T048.R005.C002]
- 开始标志性动作：球离开拍面 [CARD-GS:word/document.xml:P2431; T048.R006.C002]
- 结束标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2433; T048.R007.C002]
- 技术定义：球拍重新进入非持拍手控制。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“收拍”及阶段定义理解。 [CARD-GS:word/document.xml:P2435; T048.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2437; T048.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P2439; T048.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P2441; T048.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2443; T048.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P2445; T048.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2447; T048.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2449; T048.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2451; T048.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2453; T048.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2455; T048.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P2457; T048.R019.C002]
- AI正向反馈：球拍重新进入非持拍手控制的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P2459; T048.R020.C002]
- AI改进反馈：球拍重新进入非持拍手控制存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P2461; T048.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS01-M10 恢复准备

[CARD-GS:word/document.xml:P2464] 阶段定义：完成收拍后恢复身体平衡，双手重新建立持拍控制，调整身体姿态，建立下一拍准备状态，为后续步伐技术及下一次击球创造条件。

[CARD-GS:word/document.xml:P2465] 开始：非持拍手重新接触球拍    结束：左右脚重新接触地面，身体建立稳定准备姿态

#### GS01-M10-01 双手重新建立持拍控制

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2476; T049.R005.C002]
- 开始标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2478; T049.R006.C002]
- 结束标志性动作：左右脚重新接触地面，身体建立稳定准备姿态 [CARD-GS:word/document.xml:P2480; T049.R007.C002]
- 技术定义：双手重新建立持拍控制。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“恢复准备”及阶段定义理解。 [CARD-GS:word/document.xml:P2482; T049.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2484; T049.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P2486; T049.R010.C002]
- 所需点：J101/J121；J103/J123 [CARD-GS:word/document.xml:P2488; T049.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2490; T049.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P2492; T049.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2494; T049.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2496; T049.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2498; T049.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2500; T049.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2502; T049.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P2504; T049.R019.C002]
- AI正向反馈：双手重新建立持拍控制完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2506; T049.R020.C002]
- AI改进反馈：双手重新建立持拍控制表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2508; T049.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M10-02 身体旋转恢复。

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2520; T050.R005.C002]
- 开始标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2522; T050.R006.C002]
- 结束标志性动作：左右脚重新接触地面，身体建立稳定准备姿态 [CARD-GS:word/document.xml:P2524; T050.R007.C002]
- 技术定义：身体旋转恢复。。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“恢复准备”及阶段定义理解。 [CARD-GS:word/document.xml:P2526; T050.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2528; T050.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P2530; T050.R010.C002]
- 所需点：J033/J034；J071/J072；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2532; T050.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2534; T050.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2536; T050.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P2538; T050.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P2540; T050.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P2542; T050.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P2544; T050.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P2546; T050.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2548; T050.R019.C002]
- AI正向反馈：身体旋转恢复。完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2550; T050.R020.C002]
- AI改进反馈：身体旋转恢复。表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2552; T050.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M10-03 重心恢复稳定

- 来源/复用：原文阶段行为（无独立行为定义，不补写新的网球技术事实） [CARD-GS:word/document.xml:P2564; T051.R005.C002]
- 开始标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2566; T051.R006.C002]
- 结束标志性动作：左右脚重新接触地面，身体建立稳定准备姿态 [CARD-GS:word/document.xml:P2568; T051.R007.C002]
- 技术定义：重心恢复稳定。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“恢复准备”及阶段定义理解。 [CARD-GS:word/document.xml:P2570; T051.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2572; T051.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P2574; T051.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P2576; T051.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2578; T051.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P2580; T051.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2582; T051.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2584; T051.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2586; T051.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2588; T051.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2590; T051.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P2592; T051.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P2594; T051.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P2596; T051.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS01-M10-04 调整身体姿态并建立下一拍准备状态

待核验源冲突：GS-CONFLICT-01。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P2608; T052.R005.C002]
- 开始标志性动作：非持拍手重新接触球拍 [CARD-GS:word/document.xml:P2610; T052.R006.C002]
- 结束标志性动作：左右脚重新接触地面，身体建立稳定准备姿态 [CARD-GS:word/document.xml:P2612; T052.R007.C002]
- 技术定义：GS-02 双手反手 [CARD-GS:word/document.xml:P2614; T052.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P2616; T052.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性 [CARD-GS:word/document.xml:P2618; T052.R010.C002]
- 所需点：J101/J121；J103/J123 [CARD-GS:word/document.xml:P2620; T052.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2622; T052.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P2624; T052.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P2626; T052.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P2628; T052.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P2630; T052.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P2632; T052.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P2634; T052.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P2636; T052.R019.C002]
- AI正向反馈：调整身体姿态并建立下一拍准备状态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2638; T052.R020.C002]
- AI改进反馈：调整身体姿态并建立下一拍准备状态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2640; T052.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

## GS02 双手反手

49 项；技术注册表映射 baseline_two_hand_backhand，名称语义相符。十阶段与五阶段不是已证实的一对一关系。

### GS02-M01 双反准备

[CARD-GS:word/document.xml:P2644] 阶段定义：观察来球，建立双手协调握拍及双反准备姿态，调整身体重心，为后续启动移动创造条件

[CARD-GS:word/document.xml:P2645] 开始：球离开对方拍面，    结束：垫步落地，

#### GS02-M01-01 盯球观察

待核验源冲突：GS-CONFLICT-02。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P2656; T053.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P2658; T053.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P2660; T053.R007.C002]
- 技术定义：建立稳定的双手握拍关系，双手共同控制球拍，形成双手反手准备握拍姿态。 [CARD-GS:word/document.xml:P2662; T053.R008.C002]
- 原文关键项：双手保持合理间距；双手稳定控制拍柄；球拍保持身体前方；拍面保持稳定；为双手共同引拍创造条件 [CARD-GS:word/document.xml:P2664; T053.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P2666; T053.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；J004；J006/J007；RK专项关键点（训练后） [CARD-GS:word/document.xml:P2668; T053.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P2670; T053.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P2672; T053.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2674; T053.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2676; T053.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2678; T053.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2680; T053.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2682; T053.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P2684; T053.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P2686; T053.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P2688; T053.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M01-02 双手建立协调握拍

待核验源冲突：GS-CONFLICT-02。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P2700; T054.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P2702; T054.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P2704; T054.R007.C002]
- 技术定义：持续观察来球位置、速度及飞行方向，建立击球信息输入。 [CARD-GS:word/document.xml:P2706; T054.R008.C002]
- 原文关键项：头部稳定；视线跟踪来球；持续观察球飞行轨迹；判断来球高度；判断来球速度及方向 [CARD-GS:word/document.xml:P2708; T054.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2710; T054.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；J004；J006/J007；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2712; T054.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P2714; T054.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2716; T054.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2718; T054.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2720; T054.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2722; T054.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2724; T054.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2726; T054.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P2728; T054.R019.C002]
- AI正向反馈：双手建立协调握拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2730; T054.R020.C002]
- AI改进反馈：双手建立协调握拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2732; T054.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M01-03 建立双反准备姿态

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P2744; T055.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P2746; T055.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P2748; T055.R007.C002]
- 技术定义：建立标准双手反手准备姿态，使身体、双手及球拍形成协调控制关系。 [CARD-GS:word/document.xml:P2750; T055.R008.C002]
- 原文关键项：双肩自然放松；身体保持中立；双手位于身体前方；球拍保持准备位置；身体保持动态准备状态 [CARD-GS:word/document.xml:P2752; T055.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P2754; T055.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P2756; T055.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2758; T055.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P2760; T055.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2762; T055.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2764; T055.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2766; T055.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2768; T055.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2770; T055.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P2772; T055.R019.C002]
- AI正向反馈：建立双反准备姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2774; T055.R020.C002]
- AI改进反馈：建立双反准备姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2776; T055.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M01-04 降低重心

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P2788; T056.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P2790; T056.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P2792; T056.R007.C002]
- 技术定义：主动降低身体重心，提高身体稳定性，为垫步及启动创造条件。 [CARD-GS:word/document.xml:P2794; T056.R008.C002]
- 原文关键项：屈膝降低身体；重心位于身体中间；身体保持平衡；双脚保持弹性支撑；为快速启动做好准备 [CARD-GS:word/document.xml:P2796; T056.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P2798; T056.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P2800; T056.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2802; T056.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P2804; T056.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2806; T056.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2808; T056.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2810; T056.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2812; T056.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2814; T056.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P2816; T056.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P2818; T056.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P2820; T056.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M01-05 完成垫步并准备启动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P2832; T057.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P2834; T057.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P2836; T057.R007.C002]
- 技术定义：完成垫步动作，建立启动基础，根据来球方向准备移动。 [CARD-GS:word/document.xml:P2838; T057.R008.C002]
- 原文关键项：左右脚完成垫步；重心重新稳定；身体保持动态平衡；根据来球方向准备启动；为GS02-M02启动移动创造条件 [CARD-GS:word/document.xml:P2840; T057.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2842; T057.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2844; T057.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2846; T057.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2848; T057.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2850; T057.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2852; T057.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2854; T057.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2856; T057.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2858; T057.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2860; T057.R019.C002]
- AI正向反馈：完成垫步并准备启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2862; T057.R020.C002]
- AI改进反馈：完成垫步并准备启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2864; T057.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M02 启动移动

[CARD-GS:word/document.xml:P2867] 阶段定义：根据来球方向，完成第一步启动及调整步伐，保持盯球和双手持拍，同时移动到最佳击球位置。

[CARD-GS:word/document.xml:P2868] 开始：左右脚重新接触地面（垫步落地）    结束：开始转体

[CARD-GS:word/document.xml:P2869] 原文复用关系：GS02-M02 与 GS01-M02 共用；本版已将 GS01-M02 的关键行为完整展开并重新编号。

#### GS02-M02-01 第一步启动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P2880; T058.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P2882; T058.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P2884; T058.R007.C002]
- 技术定义：据来球方向完成第一步启动，身体开始向击球方向移动。 [CARD-GS:word/document.xml:P2886; T058.R008.C002]
- 原文关键项：完成第一启动步；重心开始移动；确定移动方向；上肢保持平衡；身体开始位移 [CARD-GS:word/document.xml:P2888; T058.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2890; T058.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2892; T058.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2894; T058.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2896; T058.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2898; T058.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2900; T058.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2902; T058.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2904; T058.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2906; T058.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2908; T058.R019.C002]
- AI正向反馈：第一步启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2910; T058.R020.C002]
- AI改进反馈：第一步启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2912; T058.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M02-02 调整步伐

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P2924; T059.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P2926; T059.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P2928; T059.R007.C002]
- 技术定义：根据来球方向调整步伐及移动距离。 [CARD-GS:word/document.xml:P2930; T059.R008.C002]
- 原文关键项：左右脚协调移动；调整步幅；控制移动方向；控制移动距离；保持身体上肢稳定 [CARD-GS:word/document.xml:P2932; T059.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2934; T059.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2936; T059.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P2938; T059.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2940; T059.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2942; T059.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2944; T059.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2946; T059.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2948; T059.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2950; T059.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P2952; T059.R019.C002]
- AI正向反馈：调整步伐完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P2954; T059.R020.C002]
- AI改进反馈：调整步伐表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P2956; T059.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M02-03 持续盯球

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P2968; T060.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P2970; T060.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P2972; T060.R007.C002]
- 技术定义：移动过程中持续观察来球位置、速度及飞行方向。 [CARD-GS:word/document.xml:P2974; T060.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P2976; T060.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P2978; T060.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P2980; T060.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P2982; T060.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P2984; T060.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P2986; T060.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P2988; T060.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P2990; T060.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P2992; T060.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P2994; T060.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P2996; T060.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P2998; T060.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P3000; T060.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M02-04 身体移动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P3012; T061.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P3014; T061.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P3016; T061.R007.C002]
- 技术定义：身体整体随步伐向来球方向移动。 [CARD-GS:word/document.xml:P3018; T061.R008.C002]
- 原文关键项：重心持续移动；身体整体移动；保持动态平衡；控制移动节奏；保持身体稳定 [CARD-GS:word/document.xml:P3020; T061.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3022; T061.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3024; T061.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3026; T061.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3028; T061.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3030; T061.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3032; T061.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3034; T061.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3036; T061.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3038; T061.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P3040; T061.R019.C002]
- AI正向反馈：身体移动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3042; T061.R020.C002]
- AI改进反馈：身体移动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3044; T061.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M02-05 保持双手持拍

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P3056; T062.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P3058; T062.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P3060; T062.R007.C002]
- 技术定义：移动过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P3062; T062.R008.C002]
- 原文关键项：双手保持握拍；球拍保持身体前方；拍面保持稳定；双手共同控制球拍；保持准备姿态 [CARD-GS:word/document.xml:P3064; T062.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P3066; T062.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P3068; T062.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P3070; T062.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P3072; T062.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3074; T062.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3076; T062.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3078; T062.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3080; T062.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3082; T062.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P3084; T062.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3086; T062.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3088; T062.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M03 转肩引拍

[CARD-GS:word/document.xml:P3091] 阶段定义：身体开始转肩，双手共同完成引拍，球拍随身体转动进入双反引拍位置。

[CARD-GS:word/document.xml:P3092] 开始：    结束：

#### GS02-M03-01 转肩

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3103; T063.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3105; T063.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3107; T063.R007.C002]
- 技术定义：身体开始转肩，带动上半身转向击球方向。 [CARD-GS:word/document.xml:P3109; T063.R008.C002]
- 原文关键项：胸廓转动；骨盆保持稳定；保持上肢稳定 [CARD-GS:word/document.xml:P3111; T063.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3113; T063.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3115; T063.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3117; T063.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3119; T063.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3121; T063.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3123; T063.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3125; T063.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3127; T063.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3129; T063.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P3131; T063.R019.C002]
- AI正向反馈：身体转动较完整，肩部旋转连续。 [CARD-GS:word/document.xml:P3133; T063.R020.C002]
- AI改进反馈：身体转动不足或衔接偏慢，建议减少只用手臂引拍的情况。 [CARD-GS:word/document.xml:P3135; T063.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M03-02 双手共同引拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3147; T064.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3149; T064.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3151; T064.R007.C002]
- 技术定义：双手共同控制球拍完成引拍动作。 [CARD-GS:word/document.xml:P3153; T064.R008.C002]
- 原文关键项：双手共同控制球拍；球拍向击球方向侧后方移动；保持拍面稳定 [CARD-GS:word/document.xml:P3155; T064.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P3157; T064.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P3159; T064.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P3161; T064.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P3163; T064.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3165; T064.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3167; T064.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3169; T064.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3171; T064.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3173; T064.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P3175; T064.R019.C002]
- AI正向反馈：双手共同引拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3177; T064.R020.C002]
- AI改进反馈：双手共同引拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3179; T064.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M03-03 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3191; T065.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3193; T065.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3195; T065.R007.C002]
- 技术定义：转肩及引拍过程中持续观察来球。 [CARD-GS:word/document.xml:P3197; T065.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹 [CARD-GS:word/document.xml:P3199; T065.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3201; T065.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3203; T065.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P3205; T065.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3207; T065.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3209; T065.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3211; T065.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3213; T065.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3215; T065.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3217; T065.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P3219; T065.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P3221; T065.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P3223; T065.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M03-04 身体转动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3235; T066.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3237; T066.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3239; T066.R007.C002]
- 技术定义：身体随转肩完成整体转动，重心向后移动。 [CARD-GS:word/document.xml:P3241; T066.R008.C002]
- 原文关键项：胸廓转动；骨盆保持稳定；重心向后移动 [CARD-GS:word/document.xml:P3243; T066.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3245; T066.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3247; T066.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3249; T066.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3251; T066.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3253; T066.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3255; T066.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3257; T066.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3259; T066.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3261; T066.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P3263; T066.R019.C002]
- AI正向反馈：身体转动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3265; T066.R020.C002]
- AI改进反馈：身体转动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3267; T066.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M03-05 保持双手持拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3279; T067.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3281; T067.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3283; T067.R007.C002]
- 技术定义：转肩及引拍过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P3285; T067.R008.C002]
- 原文关键项：双手保持握拍；球拍保持稳定；双手共同控制球拍 [CARD-GS:word/document.xml:P3287; T067.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P3289; T067.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P3291; T067.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3293; T067.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P3295; T067.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3297; T067.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3299; T067.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3301; T067.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3303; T067.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3305; T067.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P3307; T067.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3309; T067.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3311; T067.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M04 主动蹬地

[CARD-GS:word/document.xml:P3314] 阶段定义：主动蹬地，重心向前转移，稳定支撑，建立地面反作用力。

[CARD-GS:word/document.xml:P3315] 开始：    结束：

#### GS02-M04-01 主动蹬地

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3326; T068.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3328; T068.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3330; T068.R007.C002]
- 技术定义：下肢主动发力完成蹬地动作。 [CARD-GS:word/document.xml:P3332; T068.R008.C002]
- 原文关键项：下肢主动发力；双脚稳定支撑；建立地面反作用力 [CARD-GS:word/document.xml:P3334; T068.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3336; T068.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3338; T068.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P3340; T068.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3342; T068.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3344; T068.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3346; T068.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3348; T068.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3350; T068.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3352; T068.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P3354; T068.R019.C002]
- AI正向反馈：主动蹬地完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3356; T068.R020.C002]
- AI改进反馈：主动蹬地表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3358; T068.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M04-02 重心向前移动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3370; T069.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3372; T069.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3374; T069.R007.C002]
- 技术定义：身体重心由后向前移动。 [CARD-GS:word/document.xml:P3376; T069.R008.C002]
- 原文关键项：重心向前移动；保持身体稳定 [CARD-GS:word/document.xml:P3378; T069.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3380; T069.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3382; T069.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3384; T069.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3386; T069.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3388; T069.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3390; T069.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3392; T069.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3394; T069.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3396; T069.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P3398; T069.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P3400; T069.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P3402; T069.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M04-03 稳定支撑

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3414; T070.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3416; T070.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3418; T070.R007.C002]
- 技术定义：下肢保持稳定支撑，维持身体姿态。 [CARD-GS:word/document.xml:P3420; T070.R008.C002]
- 原文关键项：双脚稳定支撑；骨盆保持稳定；身体保持平衡 [CARD-GS:word/document.xml:P3422; T070.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3424; T070.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3426; T070.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3428; T070.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3430; T070.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3432; T070.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3434; T070.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3436; T070.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3438; T070.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3440; T070.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P3442; T070.R019.C002]
- AI正向反馈：稳定支撑完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3444; T070.R020.C002]
- AI改进反馈：稳定支撑表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3446; T070.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M04-04 保持双手持拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3458; T071.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3460; T071.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3462; T071.R007.C002]
- 技术定义：蹬地过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P3464; T071.R008.C002]
- 原文关键项：双手保持握拍；球拍保持稳定；双手共同控制球拍 [CARD-GS:word/document.xml:P3466; T071.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P3468; T071.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P3470; T071.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3472; T071.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P3474; T071.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3476; T071.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3478; T071.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3480; T071.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3482; T071.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3484; T071.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P3486; T071.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3488; T071.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3490; T071.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M04-05 保持持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3502; T072.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3504; T072.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3506; T072.R007.C002]
- 技术定义：蹬地过程中持续观察来球。 [CARD-GS:word/document.xml:P3508; T072.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹 [CARD-GS:word/document.xml:P3510; T072.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3512; T072.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3514; T072.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P3516; T072.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3518; T072.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3520; T072.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3522; T072.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3524; T072.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3526; T072.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3528; T072.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P3530; T072.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P3532; T072.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P3534; T072.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M05 转髋

[CARD-GS:word/document.xml:P3537] 阶段定义：骨盆主动旋转，重心持续前移，下肢动力向上传递，建立肩髋分离。

[CARD-GS:word/document.xml:P3538] 开始：    结束：

#### GS02-M05-01 骨盆主动旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3549; T073.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3551; T073.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3553; T073.R007.C002]
- 技术定义：骨盆主动开始旋转。 [CARD-GS:word/document.xml:P3555; T073.R008.C002]
- 原文关键项：骨盆开始旋转；身体保持稳定；建立旋转趋势 [CARD-GS:word/document.xml:P3557; T073.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3559; T073.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3561; T073.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3563; T073.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3565; T073.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3567; T073.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3569; T073.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3571; T073.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3573; T073.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3575; T073.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P3577; T073.R019.C002]
- AI正向反馈：骨盆主动旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3579; T073.R020.C002]
- AI改进反馈：骨盆主动旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3581; T073.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M05-02 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3593; T074.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3595; T074.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3597; T074.R007.C002]
- 技术定义：身体重心持续向击球方向移动。 [CARD-GS:word/document.xml:P3599; T074.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡；保持稳定支撑 [CARD-GS:word/document.xml:P3601; T074.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3603; T074.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3605; T074.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3607; T074.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3609; T074.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3611; T074.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3613; T074.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3615; T074.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3617; T074.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3619; T074.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P3621; T074.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P3623; T074.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P3625; T074.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M05-03 下肢动力向上传递

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3637; T075.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3639; T075.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3641; T075.R007.C002]
- 技术定义：下肢力量向骨盆及躯干传递。 [CARD-GS:word/document.xml:P3643; T075.R008.C002]
- 原文关键项：下肢保持支撑；力量向上传递；身体保持稳定 [CARD-GS:word/document.xml:P3645; T075.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3647; T075.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3649; T075.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P3651; T075.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3653; T075.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3655; T075.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3657; T075.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3659; T075.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3661; T075.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3663; T075.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P3665; T075.R019.C002]
- AI正向反馈：下肢动力向上传递完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3667; T075.R020.C002]
- AI改进反馈：下肢动力向上传递表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3669; T075.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M05-04 保持双手持拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3681; T076.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3683; T076.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3685; T076.R007.C002]
- 技术定义：转髋过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P3687; T076.R008.C002]
- 原文关键项：双手保持握拍；球拍保持稳定；双手共同控制球拍 [CARD-GS:word/document.xml:P3689; T076.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P3691; T076.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P3693; T076.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3695; T076.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P3697; T076.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3699; T076.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3701; T076.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3703; T076.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3705; T076.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3707; T076.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P3709; T076.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3711; T076.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3713; T076.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M05-05 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3725; T077.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3727; T077.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3729; T077.R007.C002]
- 技术定义：转髋过程中持续观察来球。 [CARD-GS:word/document.xml:P3731; T077.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹 [CARD-GS:word/document.xml:P3733; T077.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3735; T077.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3737; T077.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P3739; T077.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3741; T077.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3743; T077.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3745; T077.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3747; T077.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3749; T077.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3751; T077.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P3753; T077.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P3755; T077.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P3757; T077.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M06 转胸

[CARD-GS:word/document.xml:P3760] 阶段定义：胸廓主动旋转，重心持续前移，躯干动力向上肢传递，双手保持稳定控制球拍。

[CARD-GS:word/document.xml:P3761] 开始：    结束：

#### GS02-M06-01 胸廓主动旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3772; T078.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3774; T078.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3776; T078.R007.C002]
- 技术定义：胸廓主动旋转，带动上半身转向击球方向。 [CARD-GS:word/document.xml:P3778; T078.R008.C002]
- 原文关键项：胸廓主动旋转；双肩同步转动 [CARD-GS:word/document.xml:P3780; T078.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3782; T078.R010.C002]
- 所需点：J033/J034；J071/J072；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3784; T078.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3786; T078.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3788; T078.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P3790; T078.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P3792; T078.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P3794; T078.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P3796; T078.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P3798; T078.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P3800; T078.R019.C002]
- AI正向反馈：胸廓主动旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3802; T078.R020.C002]
- AI改进反馈：胸廓主动旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3804; T078.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M06-02 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3816; T079.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3818; T079.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3820; T079.R007.C002]
- 技术定义：身体重心持续向击球方向移动。 [CARD-GS:word/document.xml:P3822; T079.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡 [CARD-GS:word/document.xml:P3824; T079.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P3826; T079.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P3828; T079.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3830; T079.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P3832; T079.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3834; T079.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3836; T079.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3838; T079.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3840; T079.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3842; T079.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P3844; T079.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P3846; T079.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P3848; T079.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M06-03 躯干动力向上肢传递

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3860; T080.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3862; T080.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3864; T080.R007.C002]
- 技术定义：胸廓旋转带动双上肢同步运动。 [CARD-GS:word/document.xml:P3866; T080.R008.C002]
- 原文关键项：躯干持续转动；双上肢同步运动；动力持续传递 [CARD-GS:word/document.xml:P3868; T080.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3870; T080.R010.C002]
- 所需点：J033/J034；J071/J072；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3872; T080.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3874; T080.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3876; T080.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P3878; T080.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P3880; T080.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P3882; T080.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P3884; T080.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P3886; T080.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P3888; T080.R019.C002]
- AI正向反馈：躯干动力向上肢传递完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3890; T080.R020.C002]
- AI改进反馈：躯干动力向上肢传递表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3892; T080.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M06-04 保持双手持拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3904; T081.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3906; T081.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3908; T081.R007.C002]
- 技术定义：转胸过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P3910; T081.R008.C002]
- 原文关键项：双手保持握拍；球拍保持稳定；双手共同控制球拍 [CARD-GS:word/document.xml:P3912; T081.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P3914; T081.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P3916; T081.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P3918; T081.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P3920; T081.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3922; T081.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3924; T081.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3926; T081.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3928; T081.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3930; T081.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P3932; T081.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P3934; T081.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P3936; T081.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M06-05 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3948; T082.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3950; T082.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3952; T082.R007.C002]
- 技术定义：转胸过程中持续观察来球。 [CARD-GS:word/document.xml:P3954; T082.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹 [CARD-GS:word/document.xml:P3956; T082.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P3958; T082.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P3960; T082.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P3962; T082.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P3964; T082.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P3966; T082.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P3968; T082.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P3970; T082.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P3972; T082.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P3974; T082.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P3976; T082.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P3978; T082.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P3980; T082.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M07 双手加速与挥拍

[CARD-GS:word/document.xml:P3983] 阶段定义：双手共同加速球拍，球拍持续接近来球，形成击球轨迹。

[CARD-GS:word/document.xml:P3984] 开始：    结束：

#### GS02-M07-01 双手共同加速

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P3995; T083.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3997; T083.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P3999; T083.R007.C002]
- 技术定义：双手共同控制球拍完成加速动作。 [CARD-GS:word/document.xml:P4001; T083.R008.C002]
- 原文关键项：双手共同发力；双手保持握拍；球拍持续加速 [CARD-GS:word/document.xml:P4003; T083.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P4005; T083.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P4007; T083.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P4009; T083.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P4011; T083.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4013; T083.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4015; T083.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4017; T083.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4019; T083.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4021; T083.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P4023; T083.R019.C002]
- AI正向反馈：双手共同加速完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P4025; T083.R020.C002]
- AI改进反馈：双手共同加速表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P4027; T083.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M07-02 球拍持续释放

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4039; T084.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4041; T084.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4043; T084.R007.C002]
- 技术定义：球拍持续向来球方向运动。 [CARD-GS:word/document.xml:P4045; T084.R008.C002]
- 原文关键项：球拍持续运动；拍面保持稳定；球拍接近来球 [CARD-GS:word/document.xml:P4047; T084.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4049; T084.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4051; T084.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P4053; T084.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4055; T084.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4057; T084.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4059; T084.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4061; T084.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4063; T084.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4065; T084.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P4067; T084.R019.C002]
- AI正向反馈：球拍持续释放的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P4069; T084.R020.C002]
- AI改进反馈：球拍持续释放存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P4071; T084.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M07-03 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4083; T085.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4085; T085.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4087; T085.R007.C002]
- 技术定义：加速过程中持续观察来球。 [CARD-GS:word/document.xml:P4089; T085.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹 [CARD-GS:word/document.xml:P4091; T085.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4093; T085.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4095; T085.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P4097; T085.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4099; T085.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4101; T085.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4103; T085.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4105; T085.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4107; T085.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4109; T085.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P4111; T085.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P4113; T085.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P4115; T085.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M07-04 身体继续转动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4127; T086.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4129; T086.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4131; T086.R007.C002]
- 技术定义：身体持续完成旋转动作。 [CARD-GS:word/document.xml:P4133; T086.R008.C002]
- 原文关键项：胸廓持续转动；骨盆持续旋转；身体保持稳定 [CARD-GS:word/document.xml:P4135; T086.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4137; T086.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4139; T086.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4141; T086.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4143; T086.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4145; T086.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4147; T086.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4149; T086.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4151; T086.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4153; T086.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P4155; T086.R019.C002]
- AI正向反馈：身体继续转动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P4157; T086.R020.C002]
- AI改进反馈：身体继续转动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P4159; T086.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M07-05 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4171; T087.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4173; T087.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4175; T087.R007.C002]
- 技术定义：身体重心持续向击球方向移动。 [CARD-GS:word/document.xml:P4177; T087.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡；保持稳定支撑 [CARD-GS:word/document.xml:P4179; T087.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P4181; T087.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P4183; T087.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4185; T087.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P4187; T087.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4189; T087.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4191; T087.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4193; T087.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4195; T087.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4197; T087.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P4199; T087.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P4201; T087.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P4203; T087.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M08 击球

[CARD-GS:word/document.xml:P4206] 阶段定义：球拍与球接触，完成击球动作，控制击球方向及拍面姿态。

[CARD-GS:word/document.xml:P4207] 开始：    结束：

#### GS02-M08-01 双手稳定击球

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4218; T088.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4220; T088.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4222; T088.R007.C002]
- 技术定义：双手共同控制球拍完成击球动作。 [CARD-GS:word/document.xml:P4224; T088.R008.C002]
- 原文关键项：双手保持握拍；双手共同控制球拍；完成击球 [CARD-GS:word/document.xml:P4226; T088.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P4228; T088.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P4230; T088.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4232; T088.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P4234; T088.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4236; T088.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4238; T088.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4240; T088.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4242; T088.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4244; T088.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P4246; T088.R019.C002]
- AI正向反馈：双手稳定击球在活动球与球拍专项事件可用后可进行正式评价。 [CARD-GS:word/document.xml:P4248; T088.R020.C002]
- AI改进反馈：双手稳定击球当前不应由AI直接猜测；需等待稳定球轨迹、球拍关键点和触球事件后再给技术结论。 [CARD-GS:word/document.xml:P4250; T088.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M08-02 球拍控制拍面

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4262; T089.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4264; T089.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4266; T089.R007.C002]
- 技术定义：球拍保持稳定拍面完成触球。 [CARD-GS:word/document.xml:P4268; T089.R008.C002]
- 原文关键项：拍面保持稳定；球拍接触球；控制击球方向 [CARD-GS:word/document.xml:P4270; T089.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4272; T089.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4274; T089.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P4276; T089.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4278; T089.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4280; T089.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4282; T089.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4284; T089.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4286; T089.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4288; T089.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P4290; T089.R019.C002]
- AI正向反馈：球拍控制拍面的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P4292; T089.R020.C002]
- AI改进反馈：球拍控制拍面存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P4294; T089.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M08-03 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4306; T090.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4308; T090.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4310; T090.R007.C002]
- 技术定义：击球过程中持续观察来球。 [CARD-GS:word/document.xml:P4312; T090.R008.C002]
- 原文关键项：头部保持稳定；持续观察来球；观察触球位置 [CARD-GS:word/document.xml:P4314; T090.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4316; T090.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4318; T090.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P4320; T090.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4322; T090.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4324; T090.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4326; T090.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4328; T090.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4330; T090.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4332; T090.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P4334; T090.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P4336; T090.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P4338; T090.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M08-04 身体保持稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4350; T091.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4352; T091.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4354; T091.R007.C002]
- 技术定义：击球过程中保持身体稳定。 [CARD-GS:word/document.xml:P4356; T091.R008.C002]
- 原文关键项：胸廓保持稳定；骨盆保持稳定；身体保持平衡 [CARD-GS:word/document.xml:P4358; T091.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P4360; T091.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P4362; T091.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4364; T091.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P4366; T091.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4368; T091.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4370; T091.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4372; T091.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4374; T091.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4376; T091.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P4378; T091.R019.C002]
- AI正向反馈：身体保持稳定完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P4380; T091.R020.C002]
- AI改进反馈：身体保持稳定表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P4382; T091.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M08-05 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4394; T092.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4396; T092.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4398; T092.R007.C002]
- 技术定义：击球过程中身体重心持续向前移动。 [CARD-GS:word/document.xml:P4400; T092.R008.C002]
- 原文关键项：重心持续前移；身体保持支撑；保持动态平衡 [CARD-GS:word/document.xml:P4402; T092.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P4404; T092.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P4406; T092.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4408; T092.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P4410; T092.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4412; T092.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4414; T092.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4416; T092.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4418; T092.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4420; T092.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P4422; T092.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P4424; T092.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P4426; T092.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M09 收拍

[CARD-GS:word/document.xml:P4429] 阶段定义：击球后身体继续旋转，重心持续前移，双手共同控制球拍完成收拍动作。

[CARD-GS:word/document.xml:P4430] 开始：    结束：

#### GS02-M09-01 双手继续控制球拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4441; T093.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4443; T093.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4445; T093.R007.C002]
- 技术定义：双手保持握拍，共同控制球拍完成收拍。 [CARD-GS:word/document.xml:P4447; T093.R008.C002]
- 原文关键项：双手保持握拍；双手共同控制球拍；收拍动作连续 [CARD-GS:word/document.xml:P4449; T093.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P4451; T093.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P4453; T093.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4455; T093.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P4457; T093.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4459; T093.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4461; T093.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4463; T093.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4465; T093.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4467; T093.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P4469; T093.R019.C002]
- AI正向反馈：双手继续控制球拍的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P4471; T093.R020.C002]
- AI改进反馈：双手继续控制球拍存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P4473; T093.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M09-02 球拍继续挥动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4485; T094.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4487; T094.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4489; T094.R007.C002]
- 技术定义：球拍沿挥拍轨迹继续运动。 [CARD-GS:word/document.xml:P4491; T094.R008.C002]
- 原文关键项：球拍持续运动；拍面保持稳定；收拍轨迹连续 [CARD-GS:word/document.xml:P4493; T094.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4495; T094.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4497; T094.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P4499; T094.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4501; T094.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4503; T094.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4505; T094.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4507; T094.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4509; T094.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4511; T094.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P4513; T094.R019.C002]
- AI正向反馈：球拍继续挥动的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P4515; T094.R020.C002]
- AI改进反馈：球拍继续挥动存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P4517; T094.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M09-03 身体继续旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4529; T095.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4531; T095.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4533; T095.R007.C002]
- 技术定义：身体继续完成旋转动作。 [CARD-GS:word/document.xml:P4535; T095.R008.C002]
- 原文关键项：胸廓继续旋转；骨盆继续旋转；身体保持稳定 [CARD-GS:word/document.xml:P4537; T095.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4539; T095.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4541; T095.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4543; T095.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4545; T095.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4547; T095.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4549; T095.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4551; T095.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4553; T095.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4555; T095.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P4557; T095.R019.C002]
- AI正向反馈：身体继续旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P4559; T095.R020.C002]
- AI改进反馈：身体继续旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P4561; T095.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M09-04 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4573; T096.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4575; T096.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4577; T096.R007.C002]
- 技术定义：身体重心持续向前移动。 [CARD-GS:word/document.xml:P4579; T096.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡；保持稳定支撑 [CARD-GS:word/document.xml:P4581; T096.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P4583; T096.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P4585; T096.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4587; T096.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P4589; T096.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4591; T096.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4593; T096.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4595; T096.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4597; T096.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4599; T096.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P4601; T096.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P4603; T096.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P4605; T096.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M09-05 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4617; T097.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4619; T097.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4621; T097.R007.C002]
- 技术定义：收拍过程中持续观察击球后的球。 [CARD-GS:word/document.xml:P4623; T097.R008.C002]
- 原文关键项：头部保持稳定；持续观察球；跟踪球飞行方向 [CARD-GS:word/document.xml:P4625; T097.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P4627; T097.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；J004；J006/J007 [CARD-GS:word/document.xml:P4629; T097.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P4631; T097.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P4633; T097.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4635; T097.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4637; T097.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4639; T097.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4641; T097.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4643; T097.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P4645; T097.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P4647; T097.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P4649; T097.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS02-M10 恢复准备

[CARD-GS:word/document.xml:P4652] 阶段定义：完成收拍动作后，恢复身体姿态及重心控制，保持双手持拍，建立准备姿态。

[CARD-GS:word/document.xml:P4653] 开始：    结束：

#### GS02-M10-01 保持双手持拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4664; T098.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4666; T098.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4668; T098.R007.C002]
- 技术定义：恢复过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P4670; T098.R008.C002]
- 原文关键项：双手保持握拍；双手共同控制球拍；球拍保持稳定 [CARD-GS:word/document.xml:P4672; T098.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P4674; T098.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P4676; T098.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4678; T098.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P4680; T098.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4682; T098.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4684; T098.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4686; T098.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4688; T098.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4690; T098.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P4692; T098.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P4694; T098.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P4696; T098.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M10-02 身体旋转恢复

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4708; T099.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4710; T099.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4712; T099.R007.C002]
- 技术定义：身体逐渐完成剩余旋转。 [CARD-GS:word/document.xml:P4714; T099.R008.C002]
- 原文关键项：胸廓旋转恢复；骨盆旋转恢复；身体保持稳定 [CARD-GS:word/document.xml:P4716; T099.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P4718; T099.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4720; T099.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4722; T099.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4724; T099.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4726; T099.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4728; T099.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4730; T099.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4732; T099.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4734; T099.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P4736; T099.R019.C002]
- AI正向反馈：身体旋转恢复完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P4738; T099.R020.C002]
- AI改进反馈：身体旋转恢复表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P4740; T099.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M10-03 重心恢复稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4752; T100.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4754; T100.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4756; T100.R007.C002]
- 技术定义：身体重心恢复稳定控制状态。 [CARD-GS:word/document.xml:P4758; T100.R008.C002]
- 原文关键项：重心恢复稳定；身体保持平衡；下肢稳定支撑 [CARD-GS:word/document.xml:P4760; T100.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P4762; T100.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P4764; T100.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4766; T100.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P4768; T100.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4770; T100.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4772; T100.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4774; T100.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4776; T100.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4778; T100.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P4780; T100.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P4782; T100.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P4784; T100.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS02-M10-04 建立准备姿态

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4796; T101.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4798; T101.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4800; T101.R007.C002]
- 技术定义：恢复标准双反准备姿态。 [CARD-GS:word/document.xml:P4802; T101.R008.C002]
- 原文关键项：身体恢复准备姿态；双手保持准备握拍；身体保持动态平衡 [CARD-GS:word/document.xml:P4804; T101.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P4806; T101.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P4808; T101.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4810; T101.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P4812; T101.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4814; T101.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4816; T101.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4818; T101.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4820; T101.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4822; T101.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P4824; T101.R019.C002]
- AI正向反馈：建立准备姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P4826; T101.R020.C002]
- AI改进反馈：建立准备姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P4828; T101.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

## GS03 单手反拍

49 项；技术注册表映射 baseline_one_hand_backhand，名称语义相符。十阶段与五阶段不是已证实的一对一关系。

### GS03-M01 单反准备

[CARD-GS:word/document.xml:P4832] 阶段定义：观察来球，建立单手反手准备姿态，非持拍手保持球拍三角区，调整身体重心，完成垫步。

[CARD-GS:word/document.xml:P4833] 开始：    结束：

#### GS03-M01-01 非持拍手保持球拍三角区

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4844; T102.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4846; T102.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4848; T102.R007.C002]
- 技术定义：非持拍手保持球拍三角区，双手共同控制球拍。 [CARD-GS:word/document.xml:P4850; T102.R008.C002]
- 原文关键项：非持拍手保持三角区；双手稳定控制球拍；球拍保持身体前方；拍面保持稳定 [CARD-GS:word/document.xml:P4852; T102.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P4854; T102.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P4856; T102.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P4858; T102.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P4860; T102.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4862; T102.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4864; T102.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4866; T102.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4868; T102.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4870; T102.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P4872; T102.R019.C002]
- AI正向反馈：非持拍手保持球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P4874; T102.R020.C002]
- AI改进反馈：非持拍手保持球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P4876; T102.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M01-02 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4888; T103.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4890; T103.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4892; T103.R007.C002]
- 技术定义：持续观察来球位置、速度及飞行方向。 [CARD-GS:word/document.xml:P4894; T103.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P4896; T103.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P4898; T103.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P4900; T103.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P4902; T103.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P4904; T103.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4906; T103.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4908; T103.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4910; T103.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4912; T103.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4914; T103.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P4916; T103.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P4918; T103.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P4920; T103.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M01-03 降低重心

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4932; T104.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4934; T104.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4936; T104.R007.C002]
- 技术定义：主动降低身体重心。 [CARD-GS:word/document.xml:P4938; T104.R008.C002]
- 原文关键项：屈膝降低身体；重心保持稳定；身体保持平衡；双脚保持弹性支撑；保持动态准备 [CARD-GS:word/document.xml:P4940; T104.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P4942; T104.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P4944; T104.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4946; T104.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P4948; T104.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4950; T104.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4952; T104.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4954; T104.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P4956; T104.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P4958; T104.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P4960; T104.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P4962; T104.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P4964; T104.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M01-04 双脚微微分离，完成垫步

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P4976; T105.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4978; T105.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P4980; T105.R007.C002]
- 技术定义：行为定义：完成垫步动作，双脚微微分离，建立稳定支撑。 [CARD-GS:word/document.xml:P4982; T105.R008.C002]
- 原文关键项：双脚离开地面又回落完成垫步；双脚微微分离；重心重新稳定；身体保持平衡；非持拍手保持球拍三角区 [CARD-GS:word/document.xml:P4984; T105.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P4986; T105.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P4988; T105.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P4990; T105.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P4992; T105.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P4994; T105.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P4996; T105.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P4998; T105.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5000; T105.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5002; T105.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P5004; T105.R019.C002]
- AI正向反馈：双脚微微分离，完成垫步完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5006; T105.R020.C002]
- AI改进反馈：双脚微微分离，完成垫步表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5008; T105.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M02 启动移动

[CARD-GS:word/document.xml:P5011] 阶段定义：完成垫步后，根据来球方向启动移动，调整步伐，保持单反准备姿态。

[CARD-GS:word/document.xml:P5012] 开始：    结束：

#### GS03-M02-01 第一步启动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5023; T106.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5025; T106.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5027; T106.R007.C002]
- 技术定义：根据来球方向完成第一步启动。 [CARD-GS:word/document.xml:P5029; T106.R008.C002]
- 原文关键项：完成第一启动步；重心开始移动；确定移动方向；身体保持平衡；身体开始位移 [CARD-GS:word/document.xml:P5031; T106.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5033; T106.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5035; T106.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5037; T106.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5039; T106.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5041; T106.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5043; T106.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5045; T106.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5047; T106.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5049; T106.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P5051; T106.R019.C002]
- AI正向反馈：第一步启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5053; T106.R020.C002]
- AI改进反馈：第一步启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5055; T106.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M02-02 调整步伐

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5067; T107.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5069; T107.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5071; T107.R007.C002]
- 技术定义：根据来球方向调整步伐及移动距离。 [CARD-GS:word/document.xml:P5073; T107.R008.C002]
- 原文关键项：左右脚协调移动；调整步幅；控制移动方向；控制移动距离；保持身体稳定 [CARD-GS:word/document.xml:P5075; T107.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5077; T107.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5079; T107.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5081; T107.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5083; T107.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5085; T107.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5087; T107.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5089; T107.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5091; T107.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5093; T107.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P5095; T107.R019.C002]
- AI正向反馈：调整步伐完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5097; T107.R020.C002]
- AI改进反馈：调整步伐表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5099; T107.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M02-03 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5111; T108.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5113; T108.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5115; T108.R007.C002]
- 技术定义：移动过程中持续观察来球。 [CARD-GS:word/document.xml:P5117; T108.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P5119; T108.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5121; T108.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5123; T108.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P5125; T108.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5127; T108.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5129; T108.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5131; T108.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5133; T108.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5135; T108.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5137; T108.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P5139; T108.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P5141; T108.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P5143; T108.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M02-04 身体移动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5155; T109.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5157; T109.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5159; T109.R007.C002]
- 技术定义：身体整体随步伐向来球方向移动。 [CARD-GS:word/document.xml:P5161; T109.R008.C002]
- 原文关键项：重心持续移动；身体整体移动；身体保持平衡；控制移动节奏；保持动态姿态 [CARD-GS:word/document.xml:P5163; T109.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5165; T109.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5167; T109.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5169; T109.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5171; T109.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5173; T109.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5175; T109.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5177; T109.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5179; T109.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5181; T109.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P5183; T109.R019.C002]
- AI正向反馈：身体移动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5185; T109.R020.C002]
- AI改进反馈：身体移动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5187; T109.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M02-05 保持非持拍手球拍三角区控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5199; T110.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5201; T110.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5203; T110.R007.C002]
- 技术定义：移动过程中非持拍手保持球拍三角区，持拍手控制拍柄。 [CARD-GS:word/document.xml:P5205; T110.R008.C002]
- 原文关键项：非持拍手保持球拍三角区；持拍手控制拍柄；球拍保持身体前方；拍面保持稳定；保持单反准备姿态 [CARD-GS:word/document.xml:P5207; T110.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P5209; T110.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P5211; T110.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P5213; T110.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P5215; T110.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5217; T110.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5219; T110.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5221; T110.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5223; T110.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5225; T110.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P5227; T110.R019.C002]
- AI正向反馈：保持非持拍手球拍三角区控制的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P5229; T110.R020.C002]
- AI改进反馈：保持非持拍手球拍三角区控制存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P5231; T110.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M03 转肩引拍

[CARD-GS:word/document.xml:P5234] 阶段定义：身体开始转肩，非持拍手保持球拍三角区，持拍手控制球拍，身体形成侧身姿态，重心向后移动。

[CARD-GS:word/document.xml:P5235] 开始：    结束：

#### GS03-M03-01 转肩，保持非持拍手保持球拍三角区

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5246; T111.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5248; T111.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5250; T111.R007.C002]
- 技术定义：身体开始转肩，非持拍手保持球拍三角区，持拍手控制拍柄，稳定球拍姿态。 [CARD-GS:word/document.xml:P5252; T111.R008.C002]
- 原文关键项：身体开始转肩；非持拍手保持球拍三角区；持拍手控制拍柄；球拍保持身体前方 [CARD-GS:word/document.xml:P5254; T111.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P5256; T111.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P5258; T111.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5260; T111.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P5262; T111.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5264; T111.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5266; T111.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5268; T111.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5270; T111.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5272; T111.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P5274; T111.R019.C002]
- AI正向反馈：身体转动较完整，肩部旋转连续。 [CARD-GS:word/document.xml:P5276; T111.R020.C002]
- AI改进反馈：身体转动不足或衔接偏慢，建议减少只用手臂引拍的情况。 [CARD-GS:word/document.xml:P5278; T111.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M03-02 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5290; T112.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5292; T112.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5294; T112.R007.C002]
- 技术定义：转肩引拍过程中持续观察来球。 [CARD-GS:word/document.xml:P5296; T112.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P5298; T112.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5300; T112.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5302; T112.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P5304; T112.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5306; T112.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5308; T112.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5310; T112.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5312; T112.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5314; T112.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5316; T112.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P5318; T112.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P5320; T112.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P5322; T112.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M03-03 身体形成侧身姿态

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5334; T113.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5336; T113.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5338; T113.R007.C002]
- 技术定义：身体随转肩逐渐形成侧身姿态。 [CARD-GS:word/document.xml:P5340; T113.R008.C002]
- 原文关键项：双肩开始转动；廓跟随转动；身体形成侧身；身体保持平衡；身体姿态稳定 [CARD-GS:word/document.xml:P5342; T113.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5344; T113.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5346; T113.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5348; T113.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5350; T113.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5352; T113.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5354; T113.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5356; T113.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5358; T113.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5360; T113.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P5362; T113.R019.C002]
- AI正向反馈：身体形成侧身姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5364; T113.R020.C002]
- AI改进反馈：身体形成侧身姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5366; T113.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M03-04 重心向后移动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5378; T114.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5380; T114.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5382; T114.R007.C002]
- 技术定义：身体重心随转肩动作向后移动。 [CARD-GS:word/document.xml:P5384; T114.R008.C002]
- 原文关键项：重心向后移动；身体保持平衡；骨盆保持稳定；身体姿态稳定；保持稳定支撑 [CARD-GS:word/document.xml:P5386; T114.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5388; T114.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5390; T114.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5392; T114.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5394; T114.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5396; T114.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5398; T114.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5400; T114.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5402; T114.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5404; T114.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P5406; T114.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P5408; T114.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P5410; T114.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M03-05 第一转肩完成引拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5422; T115.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5424; T115.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5426; T115.R007.C002]
- 技术定义：完成第一转肩动作，球拍随身体完成引拍。 [CARD-GS:word/document.xml:P5428; T115.R008.C002]
- 原文关键项：第一转肩完成；球拍完成引拍；身体形成侧身；非持拍手保持球拍三角区；保持单反引拍姿态 [CARD-GS:word/document.xml:P5430; T115.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P5432; T115.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P5434; T115.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5436; T115.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P5438; T115.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P5440; T115.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P5442; T115.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P5444; T115.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P5446; T115.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P5448; T115.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P5450; T115.R019.C002]
- AI正向反馈：身体转动较完整，肩部旋转连续。 [CARD-GS:word/document.xml:P5452; T115.R020.C002]
- AI改进反馈：身体转动不足或衔接偏慢，建议减少只用手臂引拍的情况。 [CARD-GS:word/document.xml:P5454; T115.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M04 蹬地

[CARD-GS:word/document.xml:P5457] 阶段定义：主动蹬地，重心向前上转移，稳定支撑，建立地面反作用力，非持拍手保持球拍三角区。

[CARD-GS:word/document.xml:P5458] 开始：    结束：

#### GS03-M04-01 主动蹬地

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5469; T116.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5471; T116.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5473; T116.R007.C002]
- 技术定义：下肢主动发力完成蹬地动作。 [CARD-GS:word/document.xml:P5475; T116.R008.C002]
- 原文关键项：支撑腿主动发力；下肢协调蹬地；身体开始向上运动；重心开始转移；保持身体稳定 [CARD-GS:word/document.xml:P5477; T116.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5479; T116.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5481; T116.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P5483; T116.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5485; T116.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5487; T116.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5489; T116.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5491; T116.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5493; T116.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5495; T116.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P5497; T116.R019.C002]
- AI正向反馈：主动蹬地完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5499; T116.R020.C002]
- AI改进反馈：主动蹬地表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5501; T116.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M04-02 重心向前上转移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5513; T117.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5515; T117.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5517; T117.R007.C002]
- 技术定义：身体重心由后向前上移动。 [CARD-GS:word/document.xml:P5519; T117.R008.C002]
- 原文关键项：重心向前移动；重心向上移动；身体保持平衡；骨盆保持稳定；身体姿态稳定 [CARD-GS:word/document.xml:P5521; T117.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5523; T117.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5525; T117.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5527; T117.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5529; T117.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5531; T117.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5533; T117.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5535; T117.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5537; T117.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5539; T117.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P5541; T117.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P5543; T117.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P5545; T117.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M04-03 稳定支撑

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5557; T118.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5559; T118.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5561; T118.R007.C002]
- 技术定义：下肢保持稳定支撑，维持身体平衡。 [CARD-GS:word/document.xml:P5563; T118.R008.C002]
- 原文关键项：双腿稳定支撑；身体保持平衡；重心保持稳定；下肢协调；身体姿态稳定 [CARD-GS:word/document.xml:P5565; T118.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5567; T118.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5569; T118.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5571; T118.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5573; T118.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5575; T118.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5577; T118.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5579; T118.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5581; T118.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5583; T118.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P5585; T118.R019.C002]
- AI正向反馈：稳定支撑完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5587; T118.R020.C002]
- AI改进反馈：稳定支撑表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5589; T118.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M04-04 建立地面反作用力

待核验源冲突：GS-CONFLICT-06。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5601; T119.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5603; T119.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5605; T119.R007.C002]
- 技术定义：下肢通过蹬地产生地面反作用力。 [CARD-GS:word/document.xml:P5607; T119.R008.C002]
- 原文关键项：下肢持续发力；地面反作用力建立；力量向上传递；身体保持稳定；支撑持续有效 [CARD-GS:word/document.xml:P5609; T119.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5611; T119.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5613; T119.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P5615; T119.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5617; T119.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5619; T119.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5621; T119.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5623; T119.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5625; T119.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5627; T119.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P5629; T119.R019.C002]
- AI正向反馈：建立地面反作用力完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5631; T119.R020.C002]
- AI改进反馈：建立地面反作用力表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5633; T119.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M04-05 保持非持拍手球拍三角区控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5645; T120.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5647; T120.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5649; T120.R007.C002]
- 技术定义：蹬地过程中非持拍手保持球拍三角区，持拍手控制拍柄，稳定球拍姿态。 [CARD-GS:word/document.xml:P5651; T120.R008.C002]
- 原文关键项：非持拍手保持球拍三角区；持拍手控制拍柄；球拍保持稳定；拍面保持稳定；双手协调控制球拍 [CARD-GS:word/document.xml:P5653; T120.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P5655; T120.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P5657; T120.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P5659; T120.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P5661; T120.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5663; T120.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5665; T120.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5667; T120.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5669; T120.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5671; T120.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P5673; T120.R019.C002]
- AI正向反馈：保持非持拍手球拍三角区控制的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P5675; T120.R020.C002]
- AI改进反馈：保持非持拍手球拍三角区控制存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P5677; T120.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M05 转髋

[CARD-GS:word/document.xml:P5680] 阶段定义：骨盆主动旋转，重心持续前移，下肢动力向上传递，非持拍手保持球拍三角区，准备胸廓启动。

[CARD-GS:word/document.xml:P5681] 开始：    结束：

#### GS03-M05-01 骨盆主动旋转

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5692; T121.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5694; T121.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5696; T121.R007.C002]
- 技术定义：骨盆主动旋转，带动身体动力链向上传递。 [CARD-GS:word/document.xml:P5698; T121.R008.C002]
- 原文关键项：骨盆主动旋转；身体保持稳定；下肢持续发力；重心持续转移；动力链保持连续 [CARD-GS:word/document.xml:P5700; T121.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5702; T121.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5704; T121.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P5706; T121.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5708; T121.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5710; T121.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5712; T121.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5714; T121.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5716; T121.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5718; T121.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P5720; T121.R019.C002]
- AI正向反馈：骨盆主动旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5722; T121.R020.C002]
- AI改进反馈：骨盆主动旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5724; T121.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M05-02 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5736; T122.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5738; T122.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5740; T122.R007.C002]
- 技术定义：身体重心持续向前移动。 [CARD-GS:word/document.xml:P5742; T122.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡；身体姿态稳定；支撑持续有效；保持稳定控制 [CARD-GS:word/document.xml:P5744; T122.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5746; T122.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5748; T122.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5750; T122.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5752; T122.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5754; T122.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5756; T122.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5758; T122.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5760; T122.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5762; T122.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P5764; T122.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P5766; T122.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P5768; T122.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M05-03 下肢动力向上传递

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5780; T123.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5782; T123.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5784; T123.R007.C002]
- 技术定义：下肢产生的力量持续向身体上传递。 [CARD-GS:word/document.xml:P5786; T123.R008.C002]
- 原文关键项：下肢持续发力；力量向上传递；重心向前继续 [CARD-GS:word/document.xml:P5788; T123.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P5790; T123.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P5792; T123.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P5794; T123.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P5796; T123.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P5798; T123.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P5800; T123.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P5802; T123.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P5804; T123.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P5806; T123.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P5808; T123.R019.C002]
- AI正向反馈：下肢动力向上传递完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5810; T123.R020.C002]
- AI改进反馈：下肢动力向上传递表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5812; T123.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M05-04 保持非持拍手球拍三角区

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5824; T124.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5826; T124.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5828; T124.R007.C002]
- 技术定义：转髋过程中非持拍手保持球拍三角区，持拍手控制拍柄。 [CARD-GS:word/document.xml:P5830; T124.R008.C002]
- 原文关键项：非持拍手保持球拍三角区；持拍手控制拍柄；球拍保持稳定；拍面保持稳定；保持单反动力链 [CARD-GS:word/document.xml:P5832; T124.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P5834; T124.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P5836; T124.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P5838; T124.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P5840; T124.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5842; T124.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5844; T124.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5846; T124.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5848; T124.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5850; T124.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P5852; T124.R019.C002]
- AI正向反馈：保持非持拍手球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P5854; T124.R020.C002]
- AI改进反馈：保持非持拍手球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P5856; T124.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M05-05 准备胸廓启动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5868; T125.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5870; T125.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5872; T125.R007.C002]
- 技术定义：骨盆旋转逐渐完成，胸廓准备主动旋转。 [CARD-GS:word/document.xml:P5874; T125.R008.C002]
- 原文关键项：骨盆持续旋转；胸廓准备启动；身体保持协调；动力链保持连续；保持身体稳定 [CARD-GS:word/document.xml:P5876; T125.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5878; T125.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5880; T125.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5882; T125.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5884; T125.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5886; T125.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5888; T125.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5890; T125.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5892; T125.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5894; T125.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P5896; T125.R019.C002]
- AI正向反馈：准备胸廓启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5898; T125.R020.C002]
- AI改进反馈：准备胸廓启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5900; T125.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M06 转胸

[CARD-GS:word/document.xml:P5903] 阶段定义：胸廓主动旋转，非持拍手自然离开球拍三角区，持拍手继续控制球拍，重心持续前移，准备单手加速挥拍。

[CARD-GS:word/document.xml:P5904] 开始：    结束：

#### GS03-M06-01 胸廓主动旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5915; T126.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5917; T126.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5919; T126.R007.C002]
- 技术定义：胸廓主动旋转，带动上半身继续运动。 [CARD-GS:word/document.xml:P5921; T126.R008.C002]
- 原文关键项：胸廓主动旋转；双肩继续转动；身体保持稳定；动力链持续；身体协调运动 [CARD-GS:word/document.xml:P5923; T126.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5925; T126.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5927; T126.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P5929; T126.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5931; T126.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5933; T126.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5935; T126.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5937; T126.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5939; T126.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5941; T126.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P5943; T126.R019.C002]
- AI正向反馈：胸廓主动旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P5945; T126.R020.C002]
- AI改进反馈：胸廓主动旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P5947; T126.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M06-02 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P5959; T127.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5961; T127.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P5963; T127.R007.C002]
- 技术定义：胸廓旋转过程中持续观察来球。 [CARD-GS:word/document.xml:P5965; T127.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P5967; T127.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P5969; T127.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P5971; T127.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P5973; T127.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P5975; T127.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P5977; T127.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P5979; T127.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P5981; T127.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P5983; T127.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P5985; T127.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P5987; T127.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P5989; T127.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P5991; T127.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M06-03 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6003; T128.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6005; T128.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6007; T128.R007.C002]
- 技术定义：身体重心持续向前移动。 [CARD-GS:word/document.xml:P6009; T128.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡；身体姿态稳定 [CARD-GS:word/document.xml:P6011; T128.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6013; T128.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6015; T128.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6017; T128.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6019; T128.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6021; T128.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6023; T128.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6025; T128.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6027; T128.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6029; T128.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6031; T128.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P6033; T128.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P6035; T128.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M06-04 非持拍手自然离开球拍三角区

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6047; T129.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6049; T129.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6051; T129.R007.C002]
- 技术定义：非持拍手自然离开球拍三角区，准备参与身体平衡控制。 [CARD-GS:word/document.xml:P6053; T129.R008.C002]
- 原文关键项：非持拍手离开球拍；手臂自然展开；动作自然连续 [CARD-GS:word/document.xml:P6055; T129.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P6057; T129.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P6059; T129.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6061; T129.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P6063; T129.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6065; T129.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6067; T129.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6069; T129.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6071; T129.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6073; T129.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P6075; T129.R019.C002]
- AI正向反馈：非持拍手自然离开球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P6077; T129.R020.C002]
- AI改进反馈：非持拍手自然离开球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P6079; T129.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M06-05 持拍手继续控制球拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6091; T130.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6093; T130.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6095; T130.R007.C002]
- 技术定义：持拍手继续控制球拍，完成单手反手动力链转换。 [CARD-GS:word/document.xml:P6097; T130.R008.C002]
- 原文关键项：持拍手稳定控制球拍；球拍保持稳定；拍面保持稳定；动作连续；为单手挥拍做好准备 [CARD-GS:word/document.xml:P6099; T130.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P6101; T130.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P6103; T130.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P6105; T130.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P6107; T130.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6109; T130.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6111; T130.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6113; T130.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6115; T130.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6117; T130.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P6119; T130.R019.C002]
- AI正向反馈：持拍手继续控制球拍的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P6121; T130.R020.C002]
- AI改进反馈：持拍手继续控制球拍存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P6123; T130.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M07 单手加速挥拍

[CARD-GS:word/document.xml:P6126] 阶段定义：持拍手单手加速球拍，球拍持续释放，非持拍手自然张开保持身体平衡，身体继续旋转，重心持续前移。

[CARD-GS:word/document.xml:P6127] 开始：    结束：

#### GS03-M07-01 持拍手单手加速

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6138; T131.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6140; T131.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6142; T131.R007.C002]
- 技术定义：持拍手主动加速球拍完成挥拍动作。 [CARD-GS:word/document.xml:P6144; T131.R008.C002]
- 原文关键项：持拍手主动发力；球拍持续加速；拍柄控制稳定；动作连续 [CARD-GS:word/document.xml:P6146; T131.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P6148; T131.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P6150; T131.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P6152; T131.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P6154; T131.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6156; T131.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6158; T131.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6160; T131.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6162; T131.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6164; T131.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P6166; T131.R019.C002]
- AI正向反馈：持拍手单手加速完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6168; T131.R020.C002]
- AI改进反馈：持拍手单手加速表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6170; T131.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M07-02 球拍持续释放

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6182; T132.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6184; T132.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6186; T132.R007.C002]
- 技术定义：球拍沿击球轨迹持续运动。 [CARD-GS:word/document.xml:P6188; T132.R008.C002]
- 原文关键项：球拍持续运动；拍面保持稳定；球拍接近来球；挥拍轨迹连续；保持控制 [CARD-GS:word/document.xml:P6190; T132.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P6192; T132.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6194; T132.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P6196; T132.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6198; T132.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6200; T132.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6202; T132.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6204; T132.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6206; T132.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6208; T132.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6210; T132.R019.C002]
- AI正向反馈：球拍持续释放的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P6212; T132.R020.C002]
- AI改进反馈：球拍持续释放存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P6214; T132.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M07-03 非持拍手自然张开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6226; T133.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6228; T133.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6230; T133.R007.C002]
- 技术定义：非持拍手自然张开，维持身体平衡。 [CARD-GS:word/document.xml:P6232; T133.R008.C002]
- 原文关键项：非持拍手自然张开；手臂保持放松；身体保持平衡；上肢协调；动作自然连续 [CARD-GS:word/document.xml:P6234; T133.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6236; T133.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6238; T133.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6240; T133.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6242; T133.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6244; T133.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6246; T133.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6248; T133.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6250; T133.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6252; T133.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6254; T133.R019.C002]
- AI正向反馈：非持拍手自然张开完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6256; T133.R020.C002]
- AI改进反馈：非持拍手自然张开表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6258; T133.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M07-04 身体继续旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6270; T134.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6272; T134.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6274; T134.R007.C002]
- 技术定义：身体持续完成旋转动作。 [CARD-GS:word/document.xml:P6276; T134.R008.C002]
- 原文关键项：胸廓继续旋转；骨盆持续旋转；身体保持稳定；动力链连续 [CARD-GS:word/document.xml:P6278; T134.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P6280; T134.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6282; T134.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6284; T134.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6286; T134.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6288; T134.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6290; T134.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6292; T134.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6294; T134.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6296; T134.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6298; T134.R019.C002]
- AI正向反馈：身体继续旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6300; T134.R020.C002]
- AI改进反馈：身体继续旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6302; T134.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M07-05 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6314; T135.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6316; T135.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6318; T135.R007.C002]
- 技术定义：身体重心持续向击球方向移动。 [CARD-GS:word/document.xml:P6320; T135.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡；支撑持续有效；动力链连续；身体姿态稳定 [CARD-GS:word/document.xml:P6322; T135.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6324; T135.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6326; T135.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6328; T135.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6330; T135.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6332; T135.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6334; T135.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6336; T135.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6338; T135.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6340; T135.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6342; T135.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P6344; T135.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P6346; T135.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M08 击球

[CARD-GS:word/document.xml:P6349] 阶段定义：球拍与球接触，持拍手单手完成击球，非持拍手保持身体平衡，控制拍面姿态及击球方向。

[CARD-GS:word/document.xml:P6350] 开始：    结束：

#### GS03-M08-01 单手稳定击球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6361; T136.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6363; T136.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6365; T136.R007.C002]
- 技术定义：持拍手稳定控制球拍完成击球。 [CARD-GS:word/document.xml:P6367; T136.R008.C002]
- 原文关键项：单手稳定控制球拍；拍面保持稳定；球拍与球接触；保持挥拍连续；控制击球方向 [CARD-GS:word/document.xml:P6369; T136.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P6371; T136.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6373; T136.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P6375; T136.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6377; T136.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P6379; T136.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P6381; T136.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P6383; T136.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P6385; T136.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P6387; T136.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6389; T136.R019.C002]
- AI正向反馈：单手稳定击球在活动球与球拍专项事件可用后可进行正式评价。 [CARD-GS:word/document.xml:P6391; T136.R020.C002]
- AI改进反馈：单手稳定击球当前不应由AI直接猜测；需等待稳定球轨迹、球拍关键点和触球事件后再给技术结论。 [CARD-GS:word/document.xml:P6393; T136.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M08-02 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6405; T137.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6407; T137.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6409; T137.R007.C002]
- 技术定义：击球过程中持续观察来球。 [CARD-GS:word/document.xml:P6411; T137.R008.C002]
- 原文关键项：头部保持稳定；持续观察来球；判断触球位置；观察球飞行；保持视觉跟踪 [CARD-GS:word/document.xml:P6413; T137.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P6415; T137.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6417; T137.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P6419; T137.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6421; T137.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6423; T137.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6425; T137.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6427; T137.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6429; T137.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6431; T137.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P6433; T137.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P6435; T137.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P6437; T137.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M08-03 非持拍手保持身体平衡

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6449; T138.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6451; T138.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6453; T138.R007.C002]
- 技术定义：非持拍手自然张开，维持身体平衡。 [CARD-GS:word/document.xml:P6455; T138.R008.C002]
- 原文关键项：非持拍手自然张开；上肢保持平衡；身体姿态稳 [CARD-GS:word/document.xml:P6457; T138.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6459; T138.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6461; T138.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6463; T138.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6465; T138.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6467; T138.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6469; T138.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6471; T138.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6473; T138.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6475; T138.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6477; T138.R019.C002]
- AI正向反馈：非持拍手保持身体平衡完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6479; T138.R020.C002]
- AI改进反馈：非持拍手保持身体平衡表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6481; T138.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M08-04 身体保持稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6493; T139.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6495; T139.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6497; T139.R007.C002]
- 技术定义：击球过程中身体保持稳定。 [CARD-GS:word/document.xml:P6499; T139.R008.C002]
- 原文关键项：胸廓保持稳定；骨盆保持稳定；身体姿态稳定 [CARD-GS:word/document.xml:P6501; T139.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6503; T139.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6505; T139.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6507; T139.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6509; T139.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6511; T139.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6513; T139.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6515; T139.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6517; T139.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6519; T139.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6521; T139.R019.C002]
- AI正向反馈：身体保持稳定完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6523; T139.R020.C002]
- AI改进反馈：身体保持稳定表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6525; T139.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M08-05 重心持续前移

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6537; T140.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6539; T140.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6541; T140.R007.C002]
- 技术定义：身体重心持续向击球方向移动。 [CARD-GS:word/document.xml:P6543; T140.R008.C002]
- 原文关键项：重心持续前移；身体保持平衡；支撑持续有效；动力链连续；身体姿态稳定 [CARD-GS:word/document.xml:P6545; T140.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6547; T140.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6549; T140.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6551; T140.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6553; T140.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6555; T140.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6557; T140.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6559; T140.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6561; T140.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6563; T140.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6565; T140.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P6567; T140.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P6569; T140.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M09 展翅收拍

[CARD-GS:word/document.xml:P6572] 阶段定义：击球后身体继续旋转，胸腔持续打开，背部肌群持续收紧，非持拍手自然张开，持拍手继续挥拍，形成展翅收拍姿态。

[CARD-GS:word/document.xml:P6573] 开始：    结束：

#### GS03-M09-01 持拍手继续挥拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6584; T141.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6586; T141.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6588; T141.R007.C002]
- 技术定义：持拍手控制球拍继续完成挥拍轨迹。 [CARD-GS:word/document.xml:P6590; T141.R008.C002]
- 原文关键项：持拍手继续挥拍；球拍持续运动；拍面保持稳定；挥拍轨迹连续；动作自然流畅 [CARD-GS:word/document.xml:P6592; T141.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P6594; T141.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6596; T141.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P6598; T141.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6600; T141.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6602; T141.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6604; T141.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6606; T141.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6608; T141.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6610; T141.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6612; T141.R019.C002]
- AI正向反馈：持拍手继续挥拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6614; T141.R020.C002]
- AI改进反馈：持拍手继续挥拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6616; T141.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M09-02 非持拍手自然张开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6628; T142.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6630; T142.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6632; T142.R007.C002]
- 技术定义：非持拍手自然张开，维持身体平衡。 [CARD-GS:word/document.xml:P6634; T142.R008.C002]
- 原文关键项：非持拍手自然张开；手臂舒展；身体保持平衡；上肢协调；动作连续 [CARD-GS:word/document.xml:P6636; T142.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6638; T142.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6640; T142.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6642; T142.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6644; T142.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6646; T142.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6648; T142.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6650; T142.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6652; T142.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6654; T142.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6656; T142.R019.C002]
- AI正向反馈：非持拍手自然张开完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6658; T142.R020.C002]
- AI改进反馈：非持拍手自然张开表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6660; T142.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M09-03 胸腔持续打开

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6672; T143.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6674; T143.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6676; T143.R007.C002]
- 技术定义：胸廓持续旋转并逐渐打开。 [CARD-GS:word/document.xml:P6678; T143.R008.C002]
- 原文关键项：胸廓持续打开；双肩继续展开；身体保持舒展：动作连续 [CARD-GS:word/document.xml:P6680; T143.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P6682; T143.R010.C002]
- 所需点：J033/J034；J071/J072；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6684; T143.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6686; T143.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6688; T143.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P6690; T143.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P6692; T143.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P6694; T143.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P6696; T143.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P6698; T143.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6700; T143.R019.C002]
- AI正向反馈：胸腔持续打开完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6702; T143.R020.C002]
- AI改进反馈：胸腔持续打开表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6704; T143.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M09-04 背部持续收紧

待核验源冲突：GS-CONFLICT-06。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6716; T144.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6718; T144.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6720; T144.R007.C002]
- 技术定义：背部肌群持续发力维持收拍姿态。 [CARD-GS:word/document.xml:P6722; T144.R008.C002]
- 原文关键项：背部持续收紧；身体保持稳定；上身姿态协调；动力链连续；保持展翅姿态 [CARD-GS:word/document.xml:P6724; T144.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P6726; T144.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6728; T144.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P6730; T144.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6732; T144.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6734; T144.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6736; T144.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6738; T144.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6740; T144.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6742; T144.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；视觉不能直接测量真实地面反作用力、肌力或承重 [CARD-GS:word/document.xml:P6744; T144.R019.C002]
- AI正向反馈：背部持续收紧完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6746; T144.R020.C002]
- AI改进反馈：背部持续收紧表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6748; T144.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M09-05 身体继续向场内旋转

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6760; T145.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6762; T145.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6764; T145.R007.C002]
- 技术定义：身体继续完成向场内旋转动作。 [CARD-GS:word/document.xml:P6766; T145.R008.C002]
- 原文关键项：身体继续旋转；重心持续前移；身体进入场内；身体保持平衡；收拍动作完成 [CARD-GS:word/document.xml:P6768; T145.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P6770; T145.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6772; T145.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6774; T145.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6776; T145.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6778; T145.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6780; T145.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6782; T145.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6784; T145.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6786; T145.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6788; T145.R019.C002]
- AI正向反馈：身体继续向场内旋转完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6790; T145.R020.C002]
- AI改进反馈：身体继续向场内旋转表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6792; T145.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS03-M10 恢复准备

[CARD-GS:word/document.xml:P6795] 阶段定义：完成展翅收拍后，身体旋转逐渐恢复，重心重新稳定，非持拍手重新接触球拍三角区，身体与球拍进入通用准备状态。

[CARD-GS:word/document.xml:P6796] 开始：    结束：

#### GS03-M10-01 非持拍手重新接触球拍三角区

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6807; T146.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6809; T146.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6811; T146.R007.C002]
- 技术定义：非持拍手从自然张开状态重新接触球拍三角区。 [CARD-GS:word/document.xml:P6813; T146.R008.C002]
- 原文关键项：非持拍手靠近球拍三角区；非持拍手重新接触 RK013；双手重新建立球拍控制；球拍姿态恢复稳定；动作自然连续 [CARD-GS:word/document.xml:P6815; T146.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P6817; T146.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6819; T146.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6821; T146.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6823; T146.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6825; T146.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6827; T146.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6829; T146.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6831; T146.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6833; T146.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6835; T146.R019.C002]
- AI正向反馈：非持拍手重新接触球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P6837; T146.R020.C002]
- AI改进反馈：非持拍手重新接触球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P6839; T146.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M10-02 持拍手恢复拍柄控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6851; T147.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6853; T147.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6855; T147.R007.C002]
- 技术定义：持拍手保持拍柄控制，球拍逐渐回到身体前方。 [CARD-GS:word/document.xml:P6857; T147.R008.C002]
- 原文关键项：持拍手控制拍柄；球拍逐渐回到身体前方；拍面恢复稳定；球拍运动速度降低；双手控制关系重新建立 [CARD-GS:word/document.xml:P6859; T147.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P6861; T147.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6863; T147.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P6865; T147.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6867; T147.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6869; T147.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6871; T147.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6873; T147.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6875; T147.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6877; T147.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6879; T147.R019.C002]
- AI正向反馈：持拍手恢复拍柄控制完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6881; T147.R020.C002]
- AI改进反馈：持拍手恢复拍柄控制表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6883; T147.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M10-03 身体旋转恢复

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6895; T148.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6897; T148.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6899; T148.R007.C002]
- 技术定义：身体逐渐完成剩余旋转，恢复稳定控制状态。 [CARD-GS:word/document.xml:P6901; T148.R008.C002]
- 原文关键项：胸廓旋转恢复；骨盆旋转恢复；身体朝向恢复；身体姿态稳定；上半身恢复控制 [CARD-GS:word/document.xml:P6903; T148.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P6905; T148.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P6907; T148.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6909; T148.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P6911; T148.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6913; T148.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6915; T148.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6917; T148.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6919; T148.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6921; T148.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P6923; T148.R019.C002]
- AI正向反馈：身体旋转恢复完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P6925; T148.R020.C002]
- AI改进反馈：身体旋转恢复表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P6927; T148.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M10-04 重心恢复稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6939; T149.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6941; T149.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6943; T149.R007.C002]
- 技术定义：身体重心重新稳定，双脚恢复稳定支撑。 [CARD-GS:word/document.xml:P6945; T149.R008.C002]
- 原文关键项：重心恢复稳定；双脚重新形成支撑；身体保持平衡；重心位于双脚之间；下肢稳定控制 [CARD-GS:word/document.xml:P6947; T149.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P6949; T149.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P6951; T149.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6953; T149.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P6955; T149.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P6957; T149.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P6959; T149.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P6961; T149.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P6963; T149.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P6965; T149.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P6967; T149.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P6969; T149.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P6971; T149.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS03-M10-05 建立通用准备状态

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P6983; T150.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6985; T150.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P6987; T150.R007.C002]
- 技术定义：完成单反收拍后的身体与球拍恢复，身体进入可继续启动、移动或击球的中性准备状态。 [CARD-GS:word/document.xml:P6989; T150.R008.C002]
- 原文关键项：球拍回到身体前方；双手重新建立球拍控制；身体恢复稳定姿态；重心恢复稳定 [CARD-GS:word/document.xml:P6991; T150.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P6993; T150.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P6995; T150.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P6997; T150.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P6999; T150.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7001; T150.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7003; T150.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7005; T150.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7007; T150.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7009; T150.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7011; T150.R019.C002]
- AI正向反馈：建立通用准备状态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P7013; T150.R020.C002]
- AI改进反馈：建立通用准备状态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P7015; T150.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

## GS04 反手切削

49 项；技术注册表映射 backhand_slice，名称语义相符。十阶段与五阶段不是已证实的一对一关系。

### GS04-M01 切削准备

[CARD-GS:word/document.xml:P7019] 阶段定义：观察来球，建立单手反手准备姿态，非持拍手保持球拍三角区，调整身体重心，完成垫步。

[CARD-GS:word/document.xml:P7020] 开始：    结束：

[CARD-GS:word/document.xml:P7021] 原文复用关系：GS04-M01 与 GS03-M01 共用；本版已将 GS03-M01 的关键行为完整展开并重新编号。

#### GS04-M01-01 非持拍手保持球拍三角区

- 来源/复用：复用 GS03-M01，已完整展开 [CARD-GS:word/document.xml:P7032; T151.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7034; T151.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7036; T151.R007.C002]
- 技术定义：非持拍手保持球拍三角区，双手共同控制球拍。 [CARD-GS:word/document.xml:P7038; T151.R008.C002]
- 原文关键项：非持拍手保持三角区；双手稳定控制球拍；球拍保持身体前方；拍面保持稳定 [CARD-GS:word/document.xml:P7040; T151.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7042; T151.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7044; T151.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7046; T151.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7048; T151.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7050; T151.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7052; T151.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7054; T151.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7056; T151.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7058; T151.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7060; T151.R019.C002]
- AI正向反馈：非持拍手保持球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7062; T151.R020.C002]
- AI改进反馈：非持拍手保持球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P7064; T151.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M01-02 持续盯球

- 来源/复用：复用 GS03-M01，已完整展开 [CARD-GS:word/document.xml:P7076; T152.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7078; T152.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7080; T152.R007.C002]
- 技术定义：持续观察来球位置、速度及飞行方向。 [CARD-GS:word/document.xml:P7082; T152.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P7084; T152.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7086; T152.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7088; T152.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P7090; T152.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7092; T152.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7094; T152.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7096; T152.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7098; T152.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7100; T152.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7102; T152.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P7104; T152.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P7106; T152.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P7108; T152.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M01-03 降低重心

- 来源/复用：复用 GS03-M01，已完整展开 [CARD-GS:word/document.xml:P7120; T153.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7122; T153.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7124; T153.R007.C002]
- 技术定义：主动降低身体重心。 [CARD-GS:word/document.xml:P7126; T153.R008.C002]
- 原文关键项：屈膝降低身体；重心保持稳定；身体保持平衡；双脚保持弹性支撑；保持动态准备 [CARD-GS:word/document.xml:P7128; T153.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P7130; T153.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P7132; T153.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7134; T153.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P7136; T153.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7138; T153.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7140; T153.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7142; T153.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7144; T153.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7146; T153.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P7148; T153.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P7150; T153.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P7152; T153.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M01-04 双脚微微分离，完成垫步

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS03-M01，已完整展开 [CARD-GS:word/document.xml:P7164; T154.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7166; T154.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7168; T154.R007.C002]
- 技术定义：行为定义：完成垫步动作，双脚微微分离，建立稳定支撑。 [CARD-GS:word/document.xml:P7170; T154.R008.C002]
- 原文关键项：双脚离开地面又回落完成垫步；双脚微微分离；重心重新稳定；身体保持平衡；非持拍手保持球拍三角区 [CARD-GS:word/document.xml:P7172; T154.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7174; T154.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7176; T154.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7178; T154.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7180; T154.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7182; T154.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7184; T154.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7186; T154.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7188; T154.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7190; T154.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7192; T154.R019.C002]
- AI正向反馈：双脚微微分离，完成垫步完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P7194; T154.R020.C002]
- AI改进反馈：双脚微微分离，完成垫步表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P7196; T154.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M02 启动移动

[CARD-GS:word/document.xml:P7199] 阶段定义：身体开始转肩，非持拍手保持球拍三角区，持拍手控制球拍，身体形成侧身姿态，重心向后移动，球拍随转肩进入单反切削引拍方向。

[CARD-GS:word/document.xml:P7200] 开始：    结束：

[CARD-GS:word/document.xml:P7201] 原文复用关系：GS04-M02 与 GS03-M02 共用；本版已将 GS03-M02 的关键行为完整展开并重新编号。

#### GS04-M02-01 第一步启动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS03-M02，已完整展开 [CARD-GS:word/document.xml:P7212; T155.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7214; T155.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7216; T155.R007.C002]
- 技术定义：根据来球方向完成第一步启动。 [CARD-GS:word/document.xml:P7218; T155.R008.C002]
- 原文关键项：完成第一启动步；重心开始移动；确定移动方向；身体保持平衡；身体开始位移 [CARD-GS:word/document.xml:P7220; T155.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7222; T155.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7224; T155.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7226; T155.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7228; T155.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7230; T155.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7232; T155.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7234; T155.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7236; T155.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7238; T155.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P7240; T155.R019.C002]
- AI正向反馈：第一步启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P7242; T155.R020.C002]
- AI改进反馈：第一步启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P7244; T155.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M02-02 调整步伐

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS03-M02，已完整展开 [CARD-GS:word/document.xml:P7256; T156.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7258; T156.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7260; T156.R007.C002]
- 技术定义：根据来球方向调整步伐及移动距离。 [CARD-GS:word/document.xml:P7262; T156.R008.C002]
- 原文关键项：左右脚协调移动；调整步幅；控制移动方向；控制移动距离；保持身体稳定 [CARD-GS:word/document.xml:P7264; T156.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7266; T156.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7268; T156.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7270; T156.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7272; T156.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7274; T156.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7276; T156.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7278; T156.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7280; T156.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7282; T156.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P7284; T156.R019.C002]
- AI正向反馈：调整步伐完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P7286; T156.R020.C002]
- AI改进反馈：调整步伐表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P7288; T156.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M02-03 持续盯球

- 来源/复用：复用 GS03-M02，已完整展开 [CARD-GS:word/document.xml:P7300; T157.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7302; T157.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7304; T157.R007.C002]
- 技术定义：移动过程中持续观察来球。 [CARD-GS:word/document.xml:P7306; T157.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P7308; T157.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7310; T157.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7312; T157.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P7314; T157.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7316; T157.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7318; T157.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7320; T157.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7322; T157.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7324; T157.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7326; T157.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P7328; T157.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P7330; T157.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P7332; T157.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M02-04 身体移动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS03-M02，已完整展开 [CARD-GS:word/document.xml:P7344; T158.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7346; T158.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7348; T158.R007.C002]
- 技术定义：身体整体随步伐向来球方向移动。 [CARD-GS:word/document.xml:P7350; T158.R008.C002]
- 原文关键项：重心持续移动；身体整体移动；身体保持平衡；控制移动节奏；保持动态姿态 [CARD-GS:word/document.xml:P7352; T158.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7354; T158.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7356; T158.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7358; T158.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7360; T158.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7362; T158.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7364; T158.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7366; T158.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7368; T158.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7370; T158.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P7372; T158.R019.C002]
- AI正向反馈：身体移动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P7374; T158.R020.C002]
- AI改进反馈：身体移动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P7376; T158.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M02-05 保持非持拍手球拍三角区控制

- 来源/复用：复用 GS03-M02，已完整展开 [CARD-GS:word/document.xml:P7388; T159.R005.C002]
- 开始标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7390; T159.R006.C002]
- 结束标志性动作：〔原文空白〕 [CARD-GS:word/document.xml:P7392; T159.R007.C002]
- 技术定义：移动过程中非持拍手保持球拍三角区，持拍手控制拍柄。 [CARD-GS:word/document.xml:P7394; T159.R008.C002]
- 原文关键项：非持拍手保持球拍三角区；持拍手控制拍柄；球拍保持身体前方；拍面保持稳定；保持单反准备姿态 [CARD-GS:word/document.xml:P7396; T159.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P7398; T159.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7400; T159.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7402; T159.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7404; T159.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7406; T159.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7408; T159.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7410; T159.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7412; T159.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7414; T159.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7416; T159.R019.C002]
- AI正向反馈：保持非持拍手球拍三角区控制的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7418; T159.R020.C002]
- AI改进反馈：保持非持拍手球拍三角区控制存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P7420; T159.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M03 转肩引拍

[CARD-GS:word/document.xml:P7423] 阶段定义：身体开始转肩，非持拍手保持球拍三角区，持拍手控制球拍，身体形成侧身姿态，重心向后移动，球拍随转肩进入单反切削引拍方向。

[CARD-GS:word/document.xml:P7424] 开始：第一转肩    结束：重心支撑建立

#### GS04-M03-01 转肩并保持非持拍手球拍三角区

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7435; T160.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P7437; T160.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7439; T160.R007.C002]
- 技术定义：身体开始转肩，非持拍手保持球拍三角区，持拍手控制拍柄，球拍随身体转动。 [CARD-GS:word/document.xml:P7441; T160.R008.C002]
- 原文关键项：身体开始转肩；非持拍手保持球拍三角区；持拍手控制拍柄；球拍随身体转动；拍面保持稳定 [CARD-GS:word/document.xml:P7443; T160.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7445; T160.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7447; T160.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7449; T160.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7451; T160.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7453; T160.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7455; T160.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7457; T160.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7459; T160.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7461; T160.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7463; T160.R019.C002]
- AI正向反馈：身体转动较完整，肩部旋转连续。 [CARD-GS:word/document.xml:P7465; T160.R020.C002]
- AI改进反馈：身体转动不足或衔接偏慢，建议减少只用手臂引拍的情况。 [CARD-GS:word/document.xml:P7467; T160.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M03-02 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7479; T161.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P7481; T161.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7483; T161.R007.C002]
- 技术定义：转肩引拍过程中持续观察来球。 [CARD-GS:word/document.xml:P7485; T161.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P7487; T161.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7489; T161.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7491; T161.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P7493; T161.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7495; T161.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7497; T161.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7499; T161.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7501; T161.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7503; T161.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7505; T161.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P7507; T161.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P7509; T161.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P7511; T161.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M03-03 身体形成侧身姿态

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7523; T162.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P7525; T162.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7527; T162.R007.C002]
- 技术定义：身体随转肩逐渐形成侧身姿态。 [CARD-GS:word/document.xml:P7529; T162.R008.C002]
- 原文关键项：双肩开始转动；胸廓跟随转动；身体形成侧身；身体保持平衡；身体姿态稳定 [CARD-GS:word/document.xml:P7531; T162.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P7533; T162.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P7535; T162.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7537; T162.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P7539; T162.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7541; T162.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7543; T162.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7545; T162.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7547; T162.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7549; T162.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P7551; T162.R019.C002]
- AI正向反馈：身体形成侧身姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P7553; T162.R020.C002]
- AI改进反馈：身体形成侧身姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P7555; T162.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M03-04 重心向后移动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7567; T163.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P7569; T163.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7571; T163.R007.C002]
- 技术定义：身体重心随转肩动作向后移动。 [CARD-GS:word/document.xml:P7573; T163.R008.C002]
- 原文关键项：重心向后移动；身体保持平衡；骨盆保持稳定；下肢保持支撑；重心支撑开始建立 [CARD-GS:word/document.xml:P7575; T163.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P7577; T163.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P7579; T163.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7581; T163.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P7583; T163.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7585; T163.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7587; T163.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7589; T163.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7591; T163.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7593; T163.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P7595; T163.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P7597; T163.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P7599; T163.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M03-05 球拍进入切削引拍方向

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7611; T164.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P7613; T164.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7615; T164.R007.C002]
- 技术定义：球拍随转肩进入单反切削引拍方向，拍头位置开始抬高，球拍从身体前方逐渐进入身体侧后方。 [CARD-GS:word/document.xml:P7617; T164.R008.C002]
- 原文关键项：球拍进入身体侧后方；拍头位置开始抬高；非持拍手保持球拍三角区；持拍手控制拍柄；拍面保持可控状态 [CARD-GS:word/document.xml:P7619; T164.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7621; T164.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7623; T164.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7625; T164.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7627; T164.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7629; T164.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7631; T164.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7633; T164.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7635; T164.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7637; T164.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7639; T164.R019.C002]
- AI正向反馈：球拍进入切削引拍方向的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7641; T164.R020.C002]
- AI改进反馈：球拍进入切削引拍方向存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P7643; T164.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M04 重心支撑

[CARD-GS:word/document.xml:P7646] 阶段定义：重心支撑建立，身体保持侧身姿态，非持拍手保持球拍三角区，持拍手控制球拍，身体重心保持稳定并开始向击球方向移动。

[CARD-GS:word/document.xml:P7647] 开始：重心支撑建立    结束：球拍开始进入高位引拍

#### GS04-M04-01 建立重心支撑

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7658; T165.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7660; T165.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7662; T165.R007.C002]
- 技术定义：双脚形成支撑，身体重心保持稳定。 [CARD-GS:word/document.xml:P7664; T165.R008.C002]
- 原文关键项：双脚形成稳定支撑；重心保持稳定；下肢保持控制；身体保持平衡；重心开始向击球方向移动 [CARD-GS:word/document.xml:P7666; T165.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P7668; T165.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P7670; T165.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7672; T165.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P7674; T165.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7676; T165.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7678; T165.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7680; T165.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7682; T165.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7684; T165.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P7686; T165.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P7688; T165.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P7690; T165.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M04-02 保持侧身姿态

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7702; T166.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7704; T166.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7706; T166.R007.C002]
- 技术定义：身体保持转肩后的侧身姿态。 [CARD-GS:word/document.xml:P7708; T166.R008.C002]
- 原文关键项：双肩保持侧向；胸廓保持转动姿态；骨盆保持稳定；身体姿态稳定；身体朝向保持可控 [CARD-GS:word/document.xml:P7710; T166.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P7712; T166.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P7714; T166.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P7716; T166.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P7718; T166.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7720; T166.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7722; T166.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7724; T166.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7726; T166.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7728; T166.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P7730; T166.R019.C002]
- AI正向反馈：保持侧身姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P7732; T166.R020.C002]
- AI改进反馈：保持侧身姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P7734; T166.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M04-03 保持非持拍手球拍三角区

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7746; T167.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7748; T167.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7750; T167.R007.C002]
- 技术定义：非持拍手保持球拍三角区，持拍手控制拍柄，维持切削引拍状态。 [CARD-GS:word/document.xml:P7752; T167.R008.C002]
- 原文关键项：非持拍手保持球拍三角区；持拍手控制拍柄；球拍保持稳定；拍面保持可控；双手协同控制球拍 [CARD-GS:word/document.xml:P7754; T167.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7756; T167.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7758; T167.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7760; T167.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7762; T167.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7764; T167.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7766; T167.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7768; T167.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7770; T167.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7772; T167.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7774; T167.R019.C002]
- AI正向反馈：保持非持拍手球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7776; T167.R020.C002]
- AI改进反馈：保持非持拍手球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P7778; T167.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M04-04 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7790; T168.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7792; T168.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7794; T168.R007.C002]
- 技术定义：重心支撑过程中持续观察来球。 [CARD-GS:word/document.xml:P7796; T168.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P7798; T168.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7800; T168.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7802; T168.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P7804; T168.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7806; T168.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7808; T168.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7810; T168.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7812; T168.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7814; T168.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7816; T168.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P7818; T168.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P7820; T168.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P7822; T168.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M04-05 球拍开始进入高位引拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7834; T169.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P7836; T169.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7838; T169.R007.C002]
- 技术定义：球拍从侧后方开始向高位引拍位置移动，拍头位置开始上升。 [CARD-GS:word/document.xml:P7840; T169.R008.C002]
- 原文关键项：球拍向高位移动；拍头位置开始上升；球拍保持在身体侧后方；非持拍手保持球拍三角区；持拍手控制拍柄 [CARD-GS:word/document.xml:P7842; T169.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7844; T169.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7846; T169.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7848; T169.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7850; T169.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7852; T169.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7854; T169.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7856; T169.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7858; T169.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7860; T169.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7862; T169.R019.C002]
- AI正向反馈：球拍开始进入高位引拍的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7864; T169.R020.C002]
- AI改进反馈：球拍开始进入高位引拍存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P7866; T169.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M05 高位引拍

[CARD-GS:word/document.xml:P7869] 阶段定义：球拍进入高位引拍位置，拍头高于预计击球点，非持拍手保持球拍三角区，持拍手控制拍柄，身体保持侧身姿态，建立单反切削的高位准备状态。

[CARD-GS:word/document.xml:P7870] 开始：球拍开始进入高位引拍    结束：拍面开始打开

#### GS04-M05-01 球拍进入高位引拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7881; T170.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7883; T170.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P7885; T170.R007.C002]
- 技术定义：球拍从身体侧后方继续上移，进入单反切削的高位引拍位置。 [CARD-GS:word/document.xml:P7887; T170.R008.C002]
- 原文关键项：球拍向高位移动；拍头位置上升；球拍保持在身体侧后方；持拍手控制拍柄；球拍运动保持连续 [CARD-GS:word/document.xml:P7889; T170.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7891; T170.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7893; T170.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7895; T170.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7897; T170.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7899; T170.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7901; T170.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7903; T170.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7905; T170.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7907; T170.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7909; T170.R019.C002]
- AI正向反馈：球拍进入高位引拍的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7911; T170.R020.C002]
- AI改进反馈：球拍进入高位引拍存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P7913; T170.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M05-02 拍头高于预计击球点

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7925; T171.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7927; T171.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P7929; T171.R007.C002]
- 技术定义：拍头位置高于预计击球点，为后续向前下切削轨迹建立高度差。 [CARD-GS:word/document.xml:P7931; T171.R008.C002]
- 原文关键项：拍头高于预计击球点；球拍高度持续上升；来球高度持续被判断；高位引拍位置形成；切削挥拍高度差建立 [CARD-GS:word/document.xml:P7933; T171.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P7935; T171.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P7937; T171.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7939; T171.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P7941; T171.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P7943; T171.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P7945; T171.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P7947; T171.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P7949; T171.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P7951; T171.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P7953; T171.R019.C002]
- AI正向反馈：拍头高于预计击球点的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7955; T171.R020.C002]
- AI改进反馈：拍头高于预计击球点存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P7957; T171.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M05-03 保持非持拍手球拍三角区

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P7969; T172.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P7971; T172.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P7973; T172.R007.C002]
- 技术定义：高位引拍过程中，非持拍手继续保持球拍三角区，持拍手控制拍柄，维持球拍姿态稳定。 [CARD-GS:word/document.xml:P7975; T172.R008.C002]
- 原文关键项：非持拍手保持球拍三角区；持拍手控制拍柄；双手协同控制球拍；拍面保持可控；球拍高位姿态稳定 [CARD-GS:word/document.xml:P7977; T172.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P7979; T172.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P7981; T172.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P7983; T172.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P7985; T172.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P7987; T172.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P7989; T172.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P7991; T172.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P7993; T172.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P7995; T172.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P7997; T172.R019.C002]
- AI正向反馈：保持非持拍手球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P7999; T172.R020.C002]
- AI改进反馈：保持非持拍手球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8001; T172.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M05-04 身体保持侧身支撑

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8013; T173.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P8015; T173.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P8017; T173.R007.C002]
- 技术定义：身体保持侧身姿态，重心保持稳定，为高位引拍提供身体支撑。 [CARD-GS:word/document.xml:P8019; T173.R008.C002]
- 原文关键项：身体保持侧身；胸廓保持转动姿态；骨盆保持稳定；重心保持稳定；下肢维持支撑 [CARD-GS:word/document.xml:P8021; T173.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P8023; T173.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P8025; T173.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P8027; T173.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P8029; T173.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8031; T173.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8033; T173.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8035; T173.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8037; T173.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8039; T173.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P8041; T173.R019.C002]
- AI正向反馈：身体保持侧身支撑完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8043; T173.R020.C002]
- AI改进反馈：身体保持侧身支撑表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8045; T173.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M05-05 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8057; T174.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P8059; T174.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P8061; T174.R007.C002]
- 技术定义：高位引拍过程中持续观察来球。 [CARD-GS:word/document.xml:P8063; T174.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；判断来球高度；判断来球方向；判断击球时机 [CARD-GS:word/document.xml:P8065; T174.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8067; T174.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8069; T174.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P8071; T174.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8073; T174.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8075; T174.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8077; T174.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8079; T174.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8081; T174.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8083; T174.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P8085; T174.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P8087; T174.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P8089; T174.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M06 拍面打开

[CARD-GS:word/document.xml:P8092] 阶段定义：球拍保持高位状态，拍面逐渐打开，持拍手控制拍柄，非持拍手自然离开球拍三角区，球拍形成向前下方切削挥拍方向。

[CARD-GS:word/document.xml:P8093] 开始：拍面开始打开    结束：非持拍手离开球拍三角区，球拍进入向前下切削方向

#### GS04-M06-01 拍面逐渐打开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8104; T175.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P8106; T175.R006.C002]
- 结束标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8108; T175.R007.C002]
- 技术定义：持拍手控制拍柄，使拍面由高位引拍状态逐渐打开。 [CARD-GS:word/document.xml:P8110; T175.R008.C002]
- 原文关键项：拍面逐渐打开；持拍手控制拍柄；拍面角度保持可控；球拍姿态保持稳定；拍面方向与来球关系保持可识别 [CARD-GS:word/document.xml:P8112; T175.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8114; T175.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8116; T175.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8118; T175.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8120; T175.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8122; T175.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8124; T175.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8126; T175.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8128; T175.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8130; T175.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8132; T175.R019.C002]
- AI正向反馈：拍面逐渐打开的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8134; T175.R020.C002]
- AI改进反馈：拍面逐渐打开存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8136; T175.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M06-02 球拍保持高位

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8148; T176.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P8150; T176.R006.C002]
- 结束标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8152; T176.R007.C002]
- 技术定义：球拍保持在高位引拍区域，拍头高于预计击球点。 [CARD-GS:word/document.xml:P8154; T176.R008.C002]
- 原文关键项：球拍保持高位；拍头高于预计击球点；球拍位于身体侧后方；球拍高度保持稳定；高位切削姿态保持 [CARD-GS:word/document.xml:P8156; T176.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8158; T176.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8160; T176.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8162; T176.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8164; T176.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8166; T176.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8168; T176.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8170; T176.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8172; T176.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8174; T176.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8176; T176.R019.C002]
- AI正向反馈：球拍保持高位的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8178; T176.R020.C002]
- AI改进反馈：球拍保持高位存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8180; T176.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M06-03 建立向前下切削方向

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8192; T177.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P8194; T177.R006.C002]
- 结束标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8196; T177.R007.C002]
- 技术定义：球拍由高位状态形成向前下方运动方向，建立单反切削挥拍轨迹。 [CARD-GS:word/document.xml:P8198; T177.R008.C002]
- 原文关键项：球拍形成向前下方运动方向；拍头由高位进入下切方向；挥拍方向与来球轨迹形成切削关系；拍面保持打开状态；切削轨迹开始建立 [CARD-GS:word/document.xml:P8200; T177.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8202; T177.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8204; T177.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8206; T177.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8208; T177.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P8210; T177.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P8212; T177.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P8214; T177.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P8216; T177.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P8218; T177.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8220; T177.R019.C002]
- AI正向反馈：建立向前下切削方向完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8222; T177.R020.C002]
- AI改进反馈：建立向前下切削方向表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8224; T177.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M06-04 非持拍手自然离开球拍三角区

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8236; T178.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P8238; T178.R006.C002]
- 结束标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8240; T178.R007.C002]
- 技术定义：非持拍手从球拍三角区自然离开，持拍手转为单手控制球拍。 [CARD-GS:word/document.xml:P8242; T178.R008.C002]
- 原文关键项：非持拍手离开 RK013；持拍手单手控制拍柄；球拍姿态保持稳定；非持拍手自然展开；身体保持平衡 [CARD-GS:word/document.xml:P8244; T178.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P8246; T178.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P8248; T178.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P8250; T178.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P8252; T178.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8254; T178.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8256; T178.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8258; T178.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8260; T178.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8262; T178.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P8264; T178.R019.C002]
- AI正向反馈：非持拍手自然离开球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8266; T178.R020.C002]
- AI改进反馈：非持拍手自然离开球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8268; T178.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M06-05 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8280; T179.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P8282; T179.R006.C002]
- 结束标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8284; T179.R007.C002]
- 技术定义：拍面打开过程中持续观察来球位置、高度和飞行方向。 [CARD-GS:word/document.xml:P8286; T179.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；判断来球高度；判断来球方向；判断切削击球时机 [CARD-GS:word/document.xml:P8288; T179.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8290; T179.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8292; T179.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8294; T179.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8296; T179.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8298; T179.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8300; T179.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8302; T179.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8304; T179.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8306; T179.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P8308; T179.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P8310; T179.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P8312; T179.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M07 单手切削挥拍

[CARD-GS:word/document.xml:P8315] 阶段定义：持拍手单手控制球拍沿向前下方轨迹挥拍，拍面保持打开状态，非持拍手自然张开维持身体平衡，球拍逐渐接近切削击球区域。

[CARD-GS:word/document.xml:P8316] 开始：非持拍手离开球拍三角区，球拍进入向前下切削方向    结束：球拍轨迹与预测切削击球区域开始收敛

#### GS04-M07-01 单手切削挥拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8327; T180.R005.C002]
- 开始标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8329; T180.R006.C002]
- 结束标志性动作：球拍轨迹与预测切削击球区域开始收敛 [CARD-GS:word/document.xml:P8331; T180.R007.C002]
- 技术定义：持拍手单手控制球拍完成切削挥拍动作。 [CARD-GS:word/document.xml:P8333; T180.R008.C002]
- 原文关键项：持拍手单手控制球拍；球拍持续向击球区域运动；挥拍轨迹连续；拍柄控制稳定；球拍运动方向保持可控 [CARD-GS:word/document.xml:P8335; T180.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8337; T180.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8339; T180.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P8341; T180.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8343; T180.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8345; T180.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8347; T180.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8349; T180.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8351; T180.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8353; T180.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8355; T180.R019.C002]
- AI正向反馈：单手切削挥拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8357; T180.R020.C002]
- AI改进反馈：单手切削挥拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8359; T180.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M07-02 拍面保持打开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8371; T181.R005.C002]
- 开始标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8373; T181.R006.C002]
- 结束标志性动作：球拍轨迹与预测切削击球区域开始收敛 [CARD-GS:word/document.xml:P8375; T181.R007.C002]
- 技术定义：挥拍过程中拍面保持打开状态，形成切削击球所需的拍面角度。 [CARD-GS:word/document.xml:P8377; T181.R008.C002]
- 原文关键项：拍面保持打开；拍面角度保持稳定；持拍手控制拍柄；拍面方向与来球关系保持可识别；球拍姿态保持可控 [CARD-GS:word/document.xml:P8379; T181.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8381; T181.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8383; T181.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8385; T181.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8387; T181.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8389; T181.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8391; T181.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8393; T181.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8395; T181.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8397; T181.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8399; T181.R019.C002]
- AI正向反馈：拍面保持打开的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8401; T181.R020.C002]
- AI改进反馈：拍面保持打开存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8403; T181.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M07-03 球拍沿向前下方运动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8415; T182.R005.C002]
- 开始标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8417; T182.R006.C002]
- 结束标志性动作：球拍轨迹与预测切削击球区域开始收敛 [CARD-GS:word/document.xml:P8419; T182.R007.C002]
- 技术定义：球拍由高位向前下方运动，形成单反切削挥拍轨迹。 [CARD-GS:word/document.xml:P8421; T182.R008.C002]
- 原文关键项：球拍由高位向前下方运动；拍头从高位下降；挥拍轨迹形成切削方向；球拍接近来球；向前下切削路径保持连续 [CARD-GS:word/document.xml:P8423; T182.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8425; T182.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8427; T182.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8429; T182.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8431; T182.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P8433; T182.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P8435; T182.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P8437; T182.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P8439; T182.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P8441; T182.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8443; T182.R019.C002]
- AI正向反馈：球拍沿向前下方运动的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8445; T182.R020.C002]
- AI改进反馈：球拍沿向前下方运动存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8447; T182.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M07-04 非持拍手自然张开

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8459; T183.R005.C002]
- 开始标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8461; T183.R006.C002]
- 结束标志性动作：球拍轨迹与预测切削击球区域开始收敛 [CARD-GS:word/document.xml:P8463; T183.R007.C002]
- 技术定义：非持拍手离开球拍后自然张开，维持身体平衡和上半身协调。 [CARD-GS:word/document.xml:P8465; T183.R008.C002]
- 原文关键项：非持拍手自然张开；非持拍手与 RK013 分离；上肢保持协调；身体保持平衡；动作自然连续 [CARD-GS:word/document.xml:P8467; T183.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P8469; T183.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P8471; T183.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P8473; T183.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P8475; T183.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8477; T183.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8479; T183.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8481; T183.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8483; T183.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8485; T183.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P8487; T183.R019.C002]
- AI正向反馈：非持拍手自然张开完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8489; T183.R020.C002]
- AI改进反馈：非持拍手自然张开表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8491; T183.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M07-05 球拍接近切削击球区域

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8503; T184.R005.C002]
- 开始标志性动作：非持拍手离开球拍三角区，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P8505; T184.R006.C002]
- 结束标志性动作：球拍轨迹与预测切削击球区域开始收敛 [CARD-GS:word/document.xml:P8507; T184.R007.C002]
- 技术定义：球拍沿切削轨迹逐渐接近预计击球区域，为切削击球建立触球前状态。 [CARD-GS:word/document.xml:P8509; T184.R008.C002]
- 原文关键项：球拍接近预计击球区域；拍头位置接近来球；拍面保持打开；球拍轨迹与来球轨迹开始收敛；切削击球时机接近形成 [CARD-GS:word/document.xml:P8511; T184.R009.C002]
- 当前识别点：当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8513; T184.R010.C002]
- 所需点：RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8515; T184.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8517; T184.R012.C002]
- 计算方式：球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8519; T184.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P8521; T184.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P8523; T184.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P8525; T184.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P8527; T184.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P8529; T184.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8531; T184.R019.C002]
- AI正向反馈：球拍接近切削击球区域的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8533; T184.R020.C002]
- AI改进反馈：球拍接近切削击球区域存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8535; T184.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M08 切削击球

[CARD-GS:word/document.xml:P8538] 阶段定义：球拍与球接触，持拍手单手控制球拍完成切削击球，拍面保持打开状态，球拍沿向前下方轨迹摩擦来球，形成下旋。

[CARD-GS:word/document.xml:P8539] 开始：球拍触球    结束：球离拍并产生下旋

#### GS04-M08-01 单手切削触球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8550; T185.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P8552; T185.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8554; T185.R007.C002]
- 技术定义：持拍手单手控制球拍与球接触，完成切削触球动作。 [CARD-GS:word/document.xml:P8556; T185.R008.C002]
- 原文关键项：持拍手单手控制球拍；球拍甜区接触来球；头部保持稳定；观察触球区域；触球动作连续 [CARD-GS:word/document.xml:P8558; T185.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8560; T185.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8562; T185.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P8564; T185.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8566; T185.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P8568; T185.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P8570; T185.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P8572; T185.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P8574; T185.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P8576; T185.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8578; T185.R019.C002]
- AI正向反馈：单手切削触球在活动球与球拍专项事件可用后可进行正式评价。 [CARD-GS:word/document.xml:P8580; T185.R020.C002]
- AI改进反馈：单手切削触球当前不应由AI直接猜测；需等待稳定球轨迹、球拍关键点和触球事件后再给技术结论。 [CARD-GS:word/document.xml:P8582; T185.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M08-02 拍面保持打开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8594; T186.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P8596; T186.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8598; T186.R007.C002]
- 技术定义：击球瞬间拍面保持打开状态，形成切削击球所需的拍面角度。 [CARD-GS:word/document.xml:P8600; T186.R008.C002]
- 原文关键项：拍面保持打开；拍面角度稳定；持拍手控制拍柄；球拍姿态可控；拍面方向与来球形成切削关系 [CARD-GS:word/document.xml:P8602; T186.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8604; T186.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8606; T186.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8608; T186.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8610; T186.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8612; T186.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8614; T186.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8616; T186.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8618; T186.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8620; T186.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8622; T186.R019.C002]
- AI正向反馈：拍面保持打开的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8624; T186.R020.C002]
- AI改进反馈：拍面保持打开存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8626; T186.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M08-03 球拍向前下摩擦来球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8638; T187.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P8640; T187.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8642; T187.R007.C002]
- 技术定义：球拍沿向前下方轨迹运动，与来球形成切削摩擦。 [CARD-GS:word/document.xml:P8644; T187.R008.C002]
- 原文关键项：球拍向前下方运动；拍头从高位向下运动；球拍与来球形成摩擦；挥拍轨迹保持连续；切削路径保持稳定 [CARD-GS:word/document.xml:P8646; T187.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8648; T187.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8650; T187.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8652; T187.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8654; T187.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8656; T187.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8658; T187.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8660; T187.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8662; T187.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8664; T187.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8666; T187.R019.C002]
- AI正向反馈：球拍向前下摩擦来球的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8668; T187.R020.C002]
- AI改进反馈：球拍向前下摩擦来球存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8670; T187.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M08-04 球产生下旋

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8682; T188.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P8684; T188.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8686; T188.R007.C002]
- 技术定义：球离开拍面后形成下旋飞行状态。 [CARD-GS:word/document.xml:P8688; T188.R008.C002]
- 原文关键项：球离开拍面；球产生下旋；球飞行方向形成；球速发生变化；旋转轴可被识别 [CARD-GS:word/document.xml:P8690; T188.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8692; T188.R010.C002]
- 所需点：J033/J034；J071/J072；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8694; T188.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P8696; T188.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8698; T188.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P8700; T188.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P8702; T188.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P8704; T188.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P8706; T188.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P8708; T188.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8710; T188.R019.C002]
- AI正向反馈：球产生下旋完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8712; T188.R020.C002]
- AI改进反馈：球产生下旋表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8714; T188.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M08-05 非持拍手张开并保持身体稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8726; T189.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P8728; T189.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8730; T189.R007.C002]
- 技术定义：非持拍手自然张开，身体保持稳定支撑，完成切削击球瞬间的身体控制。 [CARD-GS:word/document.xml:P8732; T189.R008.C002]
- 原文关键项：非持拍手自然张开；身体保持稳定；胸廓保持控制；骨盆保持稳定；重心保持可控 [CARD-GS:word/document.xml:P8734; T189.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P8736; T189.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163 [CARD-GS:word/document.xml:P8738; T189.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P8740; T189.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P8742; T189.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8744; T189.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8746; T189.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8748; T189.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8750; T189.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8752; T189.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P8754; T189.R019.C002]
- AI正向反馈：非持拍手张开并保持身体稳定完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8756; T189.R020.C002]
- AI改进反馈：非持拍手张开并保持身体稳定表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8758; T189.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M09 切削收拍

[CARD-GS:word/document.xml:P8761] 阶段定义：球离拍后，持拍手继续控制球拍沿向前下方轨迹完成收拍，拍头位置下降，拍面保持可控，非持拍手自然张开，身体保持稳定，完成低位切削收拍动作。

[CARD-GS:word/document.xml:P8762] 开始：球离拍并产生下旋    结束：低位切削收拍完成

#### GS04-M09-01 持拍手继续完成切削挥拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8773; T190.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8775; T190.R006.C002]
- 结束标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P8777; T190.R007.C002]
- 技术定义：球离拍后，持拍手继续控制球拍完成切削挥拍轨迹。 [CARD-GS:word/document.xml:P8779; T190.R008.C002]
- 原文关键项：持拍手继续控制球拍；球拍继续沿切削方向运动；挥拍轨迹连续；拍柄控制稳定；球拍速度逐渐降低 [CARD-GS:word/document.xml:P8781; T190.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8783; T190.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8785; T190.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P8787; T190.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8789; T190.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P8791; T190.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P8793; T190.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P8795; T190.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P8797; T190.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P8799; T190.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8801; T190.R019.C002]
- AI正向反馈：持拍手继续完成切削挥拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8803; T190.R020.C002]
- AI改进反馈：持拍手继续完成切削挥拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8805; T190.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M09-02 拍头继续下降

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8817; T191.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8819; T191.R006.C002]
- 结束标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P8821; T191.R007.C002]
- 技术定义：球拍在收拍过程中继续向低位运动，拍头位置下降。 [CARD-GS:word/document.xml:P8823; T191.R008.C002]
- 原文关键项：拍头继续下降；球拍由高位进入低位；球拍运动方向保持可识别；收拍位置低于击球前高位引拍；切削收拍轨迹形成 [CARD-GS:word/document.xml:P8825; T191.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P8827; T191.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P8829; T191.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8831; T191.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P8833; T191.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P8835; T191.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P8837; T191.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P8839; T191.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P8841; T191.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P8843; T191.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P8845; T191.R019.C002]
- AI正向反馈：拍头继续下降的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8847; T191.R020.C002]
- AI改进反馈：拍头继续下降存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8849; T191.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M09-03 拍面保持可控

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8861; T192.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8863; T192.R006.C002]
- 结束标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P8865; T192.R007.C002]
- 技术定义：收拍过程中拍面保持可控状态，避免球拍姿态失控。 [CARD-GS:word/document.xml:P8867; T192.R008.C002]
- 原文关键项：拍面保持可控；拍面角度逐渐恢复；持拍手控制拍柄；球拍姿态保持稳定；拍面方向变化可识别 [CARD-GS:word/document.xml:P8869; T192.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P8871; T192.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P8873; T192.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P8875; T192.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P8877; T192.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8879; T192.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8881; T192.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8883; T192.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8885; T192.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8887; T192.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P8889; T192.R019.C002]
- AI正向反馈：拍面保持可控的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P8891; T192.R020.C002]
- AI改进反馈：拍面保持可控存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P8893; T192.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M09-04 非持拍手自然张开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8905; T193.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8907; T193.R006.C002]
- 结束标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P8909; T193.R007.C002]
- 技术定义：非持拍手保持自然张开状态，维持身体平衡和上半身协调。 [CARD-GS:word/document.xml:P8911; T193.R008.C002]
- 原文关键项：非持拍手自然张开；非持拍手与 RK013 保持分离；上肢保持协调；身体保持平衡；身体姿态稳定 [CARD-GS:word/document.xml:P8913; T193.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P8915; T193.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P8917; T193.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P8919; T193.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P8921; T193.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8923; T193.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8925; T193.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8927; T193.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8929; T193.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8931; T193.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P8933; T193.R019.C002]
- AI正向反馈：非持拍手自然张开完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8935; T193.R020.C002]
- AI改进反馈：非持拍手自然张开表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8937; T193.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M09-05 身体保持稳定并完成低位收拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8949; T194.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P8951; T194.R006.C002]
- 结束标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P8953; T194.R007.C002]
- 技术定义：身体保持稳定控制，持拍手完成低位切削收拍动作。 [CARD-GS:word/document.xml:P8955; T194.R008.C002]
- 原文关键项：身体保持稳定；胸廓保持控制；骨盆保持稳定；重心保持可控；低位切削收拍完成 [CARD-GS:word/document.xml:P8957; T194.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P8959; T194.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163 [CARD-GS:word/document.xml:P8961; T194.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P8963; T194.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P8965; T194.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P8967; T194.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P8969; T194.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P8971; T194.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P8973; T194.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P8975; T194.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P8977; T194.R019.C002]
- AI正向反馈：身体保持稳定并完成低位收拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P8979; T194.R020.C002]
- AI改进反馈：身体保持稳定并完成低位收拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P8981; T194.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS04-M10 恢复准备

[CARD-GS:word/document.xml:P8984] 阶段定义：完成低位切削收拍后，身体姿态逐渐恢复，重心重新稳定，非持拍手重新接触球拍三角区，身体与球拍进入通用准备状态。

[CARD-GS:word/document.xml:P8985] 开始：低位切削收拍完成    结束：身体恢复稳定通用准备状态

#### GS04-M10-01 非持拍手重新接触球拍三角区

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P8996; T195.R005.C002]
- 开始标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P8998; T195.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P9000; T195.R007.C002]
- 技术定义：非持拍手从自然张开状态重新靠近球拍，并重新接触球拍三角区。 [CARD-GS:word/document.xml:P9002; T195.R008.C002]
- 原文关键项：非持拍手靠近球拍三角区；非持拍手重新接触 RK013；双手重新建立球拍控制；球拍姿态恢复稳定；动作自然连续 [CARD-GS:word/document.xml:P9004; T195.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P9006; T195.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P9008; T195.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9010; T195.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P9012; T195.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9014; T195.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9016; T195.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9018; T195.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9020; T195.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9022; T195.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P9024; T195.R019.C002]
- AI正向反馈：非持拍手重新接触球拍三角区的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P9026; T195.R020.C002]
- AI改进反馈：非持拍手重新接触球拍三角区存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P9028; T195.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M10-02 持拍手恢复拍柄控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9040; T196.R005.C002]
- 开始标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P9042; T196.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P9044; T196.R007.C002]
- 技术定义：持拍手保持拍柄控制，球拍运动速度降低，球拍逐渐脱离低位收拍状态。 [CARD-GS:word/document.xml:P9046; T196.R008.C002]
- 原文关键项：持拍手控制拍柄；球拍运动速度降低；拍面恢复可控；球拍姿态恢复稳定；双手控制关系重新建立 [CARD-GS:word/document.xml:P9048; T196.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P9050; T196.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P9052; T196.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P9054; T196.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P9056; T196.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9058; T196.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9060; T196.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9062; T196.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9064; T196.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9066; T196.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P9068; T196.R019.C002]
- AI正向反馈：持拍手恢复拍柄控制完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9070; T196.R020.C002]
- AI改进反馈：持拍手恢复拍柄控制表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9072; T196.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M10-03 球拍从低位回到身体前方

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9084; T197.R005.C002]
- 开始标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P9086; T197.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P9088; T197.R007.C002]
- 技术定义：球拍从低位切削收拍位置逐渐回到身体前方，恢复准备状态下的球拍位置。 [CARD-GS:word/document.xml:P9090; T197.R008.C002]
- 原文关键项：球拍从低位抬起；球拍回到身体前方；拍头高度恢复；球拍与身体距离恢复；球拍姿态保持可控 [CARD-GS:word/document.xml:P9092; T197.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P9094; T197.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P9096; T197.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P9098; T197.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P9100; T197.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P9102; T197.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P9104; T197.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P9106; T197.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P9108; T197.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P9110; T197.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P9112; T197.R019.C002]
- AI正向反馈：球拍从低位回到身体前方的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P9114; T197.R020.C002]
- AI改进反馈：球拍从低位回到身体前方存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P9116; T197.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M10-04 重心恢复稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9128; T198.R005.C002]
- 开始标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P9130; T198.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P9132; T198.R007.C002]
- 技术定义：身体重心重新稳定，双脚恢复稳定支撑。 [CARD-GS:word/document.xml:P9134; T198.R008.C002]
- 原文关键项：重心恢复稳定；双脚重新形成支撑；身体保持平衡；重心位于双脚之间；下肢稳定控制 [CARD-GS:word/document.xml:P9136; T198.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P9138; T198.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P9140; T198.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9142; T198.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P9144; T198.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9146; T198.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9148; T198.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9150; T198.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9152; T198.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9154; T198.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9156; T198.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P9158; T198.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P9160; T198.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS04-M10-05 建立通用准备状态

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9172; T199.R005.C002]
- 开始标志性动作：低位切削收拍完成 [CARD-GS:word/document.xml:P9174; T199.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P9176; T199.R007.C002]
- 技术定义：完成切削收拍后的身体与球拍恢复，身体进入可继续启动、移动或击球的中性准备状态。 [CARD-GS:word/document.xml:P9178; T199.R008.C002]
- 原文关键项：球拍回到身体前方；双手重新建立球拍控制；身体恢复稳定姿态；重心恢复稳定；不限定下一拍技术类型 [CARD-GS:word/document.xml:P9180; T199.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P9182; T199.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P9184; T199.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9186; T199.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P9188; T199.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9190; T199.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9192; T199.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9194; T199.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9196; T199.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9198; T199.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P9200; T199.R019.C002]
- AI正向反馈：建立通用准备状态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9202; T199.R020.C002]
- AI改进反馈：建立通用准备状态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9204; T199.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

## GS05 正手切削

50 项；技术注册表映射 forehand_slice，名称语义相符。十阶段与五阶段不是已证实的一对一关系。

### GS05-M01 正手切削准备

[CARD-GS:word/document.xml:P9208] 阶段定义：建立准备姿态，观察来球，与垫步启动

[CARD-GS:word/document.xml:P9209] 开始：球离开对方拍面，    结束：垫步落地，

[CARD-GS:word/document.xml:P9210] 原文复用关系：GS05-M01 与 GS01-M01 共用；本版已将 GS01-M01 的关键行为完整展开并重新编号。

#### GS05-M01-01 双手持拍

待核验源冲突：GS-CONFLICT-03。

- 来源/复用：复用 GS01-M01，已完整展开 [CARD-GS:word/document.xml:P9221; T200.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P9223; T200.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P9225; T200.R007.C002]
- 技术定义：双手持拍。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“正手切削准备”及阶段定义理解。 [CARD-GS:word/document.xml:P9227; T200.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P9229; T200.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性 [CARD-GS:word/document.xml:P9231; T200.R010.C002]
- 所需点：J101/J121；J103/J123 [CARD-GS:word/document.xml:P9233; T200.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9235; T200.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 [CARD-GS:word/document.xml:P9237; T200.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P9239; T200.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P9241; T200.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P9243; T200.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P9245; T200.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P9247; T200.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9249; T200.R019.C002]
- AI正向反馈：双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9251; T200.R020.C002]
- AI改进反馈：双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9253; T200.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M01-02 持续盯球

- 来源/复用：复用 GS01-M01，已完整展开 [CARD-GS:word/document.xml:P9265; T201.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P9267; T201.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P9269; T201.R007.C002]
- 技术定义：持续盯球。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“正手切削准备”及阶段定义理解。 [CARD-GS:word/document.xml:P9271; T201.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P9273; T201.R009.C002]
- 当前识别点：头部位置与朝向稳定度；真实视线仅作近似 [CARD-GS:word/document.xml:P9275; T201.R010.C002]
- 所需点：J004；J006/J007 [CARD-GS:word/document.xml:P9277; T201.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P9279; T201.R012.C002]
- 计算方式：人体关键点时序变化 + 动作连续性 [CARD-GS:word/document.xml:P9281; T201.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P9283; T201.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P9285; T201.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P9287; T201.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P9289; T201.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P9291; T201.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P9293; T201.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P9295; T201.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P9297; T201.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M01-03 降低重心

待核验源冲突：GS-CONFLICT-03。

- 来源/复用：复用 GS01-M01，已完整展开 [CARD-GS:word/document.xml:P9309; T202.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P9311; T202.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P9313; T202.R007.C002]
- 技术定义：降低重心。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“正手切削准备”及阶段定义理解。 [CARD-GS:word/document.xml:P9315; T202.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P9317; T202.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P9319; T202.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P9321; T202.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9323; T202.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P9325; T202.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P9327; T202.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P9329; T202.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P9331; T202.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P9333; T202.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P9335; T202.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9337; T202.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P9339; T202.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P9341; T202.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M01-04 完成垫步

待核验源冲突：GS-CONFLICT-03。

- 来源/复用：复用 GS01-M01，已完整展开 [CARD-GS:word/document.xml:P9353; T203.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P9355; T203.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P9357; T203.R007.C002]
- 技术定义：完成垫步。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“正手切削准备”及阶段定义理解。 [CARD-GS:word/document.xml:P9359; T203.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P9361; T203.R009.C002]
- 当前识别点：人体关键点相对位置、速度、持续时间与动作连续性 [CARD-GS:word/document.xml:P9363; T203.R010.C002]
- 所需点：COCO-17目标球员骨架 [CARD-GS:word/document.xml:P9365; T203.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9367; T203.R012.C002]
- 计算方式：人体关键点时序变化 + 动作连续性 [CARD-GS:word/document.xml:P9369; T203.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P9371; T203.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P9373; T203.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P9375; T203.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P9377; T203.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P9379; T203.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9381; T203.R019.C002]
- AI正向反馈：完成垫步完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9383; T203.R020.C002]
- AI改进反馈：完成垫步表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9385; T203.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M01-05 准备启动

待核验源冲突：GS-CONFLICT-03。

- 来源/复用：复用 GS01-M01，已完整展开 [CARD-GS:word/document.xml:P9397; T204.R005.C002]
- 开始标志性动作：球离开对方拍面， [CARD-GS:word/document.xml:P9399; T204.R006.C002]
- 结束标志性动作：垫步落地， [CARD-GS:word/document.xml:P9401; T204.R007.C002]
- 技术定义：准备启动。该项为原文阶段行为；原文未另列独立行为定义，技术含义按所属阶段“正手切削准备”及阶段定义理解。 [CARD-GS:word/document.xml:P9403; T204.R008.C002]
- 原文关键项：原文未另列独立关键项；以阶段行为名称和所属阶段定义为原始依据 [CARD-GS:word/document.xml:P9405; T204.R009.C002]
- 当前识别点：人体关键点相对位置、速度、持续时间与动作连续性 [CARD-GS:word/document.xml:P9407; T204.R010.C002]
- 所需点：COCO-17目标球员骨架 [CARD-GS:word/document.xml:P9409; T204.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9411; T204.R012.C002]
- 计算方式：人体关键点时序变化 + 动作连续性 [CARD-GS:word/document.xml:P9413; T204.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P9415; T204.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P9417; T204.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P9419; T204.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P9421; T204.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P9423; T204.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9425; T204.R019.C002]
- AI正向反馈：准备启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9427; T204.R020.C002]
- AI改进反馈：准备启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9429; T204.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M02 启动移动

[CARD-GS:word/document.xml:P9432] 阶段定义：根据来球方向，完成第一步启动及调整步伐，保持盯球和双手持拍，同时移动到最佳击球位置。

[CARD-GS:word/document.xml:P9433] 开始：左右脚重新接触地面（垫步落地）    结束：开始转体

[CARD-GS:word/document.xml:P9434] 原文复用关系：GS05-M02 与 GS01-M02 共用；本版已将 GS01-M02 的关键行为完整展开并重新编号。

#### GS05-M02-01 第一步启动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P9445; T205.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P9447; T205.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P9449; T205.R007.C002]
- 技术定义：据来球方向完成第一步启动，身体开始向击球方向移动。 [CARD-GS:word/document.xml:P9451; T205.R008.C002]
- 原文关键项：完成第一启动步；重心开始移动；确定移动方向；上肢保持平衡；身体开始位移 [CARD-GS:word/document.xml:P9453; T205.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P9455; T205.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P9457; T205.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9459; T205.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P9461; T205.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9463; T205.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9465; T205.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9467; T205.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9469; T205.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9471; T205.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P9473; T205.R019.C002]
- AI正向反馈：第一步启动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9475; T205.R020.C002]
- AI改进反馈：第一步启动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9477; T205.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M02-02 调整步伐

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P9489; T206.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P9491; T206.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P9493; T206.R007.C002]
- 技术定义：根据来球方向调整步伐及移动距离。 [CARD-GS:word/document.xml:P9495; T206.R008.C002]
- 原文关键项：左右脚协调移动；调整步幅；控制移动方向；控制移动距离；保持身体上肢稳定 [CARD-GS:word/document.xml:P9497; T206.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P9499; T206.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P9501; T206.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9503; T206.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P9505; T206.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9507; T206.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9509; T206.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9511; T206.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9513; T206.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9515; T206.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P9517; T206.R019.C002]
- AI正向反馈：调整步伐完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9519; T206.R020.C002]
- AI改进反馈：调整步伐表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9521; T206.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M02-03 持续盯球

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P9533; T207.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P9535; T207.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P9537; T207.R007.C002]
- 技术定义：移动过程中持续观察来球位置、速度及飞行方向。 [CARD-GS:word/document.xml:P9539; T207.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P9541; T207.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P9543; T207.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P9545; T207.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P9547; T207.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P9549; T207.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9551; T207.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9553; T207.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9555; T207.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9557; T207.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9559; T207.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P9561; T207.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P9563; T207.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P9565; T207.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M02-04 身体移动

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P9577; T208.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P9579; T208.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P9581; T208.R007.C002]
- 技术定义：身体整体随步伐向来球方向移动。 [CARD-GS:word/document.xml:P9583; T208.R008.C002]
- 原文关键项：重心持续移动；身体整体移动；保持动态平衡；控制移动节奏；保持身体稳定 [CARD-GS:word/document.xml:P9585; T208.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P9587; T208.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P9589; T208.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9591; T208.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P9593; T208.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9595; T208.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9597; T208.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9599; T208.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9601; T208.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9603; T208.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P9605; T208.R019.C002]
- AI正向反馈：身体移动完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9607; T208.R020.C002]
- AI改进反馈：身体移动表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9609; T208.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M02-05 保持双手持拍

- 来源/复用：复用 GS01-M02，已完整展开 [CARD-GS:word/document.xml:P9621; T209.R005.C002]
- 开始标志性动作：左右脚重新接触地面（垫步落地） [CARD-GS:word/document.xml:P9623; T209.R006.C002]
- 结束标志性动作：开始转体 [CARD-GS:word/document.xml:P9625; T209.R007.C002]
- 技术定义：移动过程中保持双手稳定控制球拍。 [CARD-GS:word/document.xml:P9627; T209.R008.C002]
- 原文关键项：双手保持握拍；球拍保持身体前方；拍面保持稳定；双手共同控制球拍；保持准备姿态 [CARD-GS:word/document.xml:P9629; T209.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P9631; T209.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P9633; T209.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P9635; T209.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P9637; T209.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9639; T209.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9641; T209.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9643; T209.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9645; T209.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9647; T209.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P9649; T209.R019.C002]
- AI正向反馈：保持双手持拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9651; T209.R020.C002]
- AI改进反馈：保持双手持拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9653; T209.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M03 转肩引拍

[CARD-GS:word/document.xml:P9656] 阶段定义：身体开始转肩，非持拍手辅助球拍控制，持拍手控制拍柄，身体形成侧身姿态，重心向后移动，球拍随转肩进入正手切削引拍方向。

[CARD-GS:word/document.xml:P9657] 开始：第一转肩    结束：重心支撑建立

#### GS05-M03-01 转肩并保持球拍控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9668; T210.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P9670; T210.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9672; T210.R007.C002]
- 技术定义：身体开始转肩，非持拍手辅助球拍控制，持拍手控制拍柄，球拍随身体转动。 [CARD-GS:word/document.xml:P9674; T210.R008.C002]
- 原文关键项：身体开始转肩；非持拍手辅助球拍控制；持拍手控制拍柄；球拍随身体转动；拍面保持可控状态 [CARD-GS:word/document.xml:P9676; T210.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P9678; T210.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P9680; T210.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P9682; T210.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P9684; T210.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9686; T210.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9688; T210.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9690; T210.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9692; T210.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9694; T210.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P9696; T210.R019.C002]
- AI正向反馈：身体转动较完整，肩部旋转连续。 [CARD-GS:word/document.xml:P9698; T210.R020.C002]
- AI改进反馈：身体转动不足或衔接偏慢，建议减少只用手臂引拍的情况。 [CARD-GS:word/document.xml:P9700; T210.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M03-02 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9712; T211.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P9714; T211.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9716; T211.R007.C002]
- 技术定义：转肩引拍过程中持续观察来球。 [CARD-GS:word/document.xml:P9718; T211.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P9720; T211.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P9722; T211.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P9724; T211.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P9726; T211.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P9728; T211.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9730; T211.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9732; T211.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9734; T211.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9736; T211.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9738; T211.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P9740; T211.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P9742; T211.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P9744; T211.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M03-03 身体形成侧身姿态

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9756; T212.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P9758; T212.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9760; T212.R007.C002]
- 技术定义：身体随转肩逐渐形成正手侧身姿态。 [CARD-GS:word/document.xml:P9762; T212.R008.C002]
- 原文关键项：双肩开始转动；胸廓跟随转动；身体形成侧身；骨盆保持稳定；身体姿态稳定 [CARD-GS:word/document.xml:P9764; T212.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P9766; T212.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P9768; T212.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9770; T212.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P9772; T212.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9774; T212.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9776; T212.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9778; T212.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9780; T212.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9782; T212.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9784; T212.R019.C002]
- AI正向反馈：身体形成侧身姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9786; T212.R020.C002]
- AI改进反馈：身体形成侧身姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9788; T212.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M03-04 重心向后移动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9800; T213.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P9802; T213.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9804; T213.R007.C002]
- 技术定义：身体重心随转肩动作向后移动。 [CARD-GS:word/document.xml:P9806; T213.R008.C002]
- 原文关键项：重心向后移动；身体保持平衡；骨盆保持稳定；下肢保持支撑；重心支撑开始建立 [CARD-GS:word/document.xml:P9808; T213.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P9810; T213.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P9812; T213.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9814; T213.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P9816; T213.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9818; T213.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9820; T213.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9822; T213.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9824; T213.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9826; T213.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9828; T213.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P9830; T213.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P9832; T213.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M03-05 球拍进入正手切削引拍方向

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9844; T214.R005.C002]
- 开始标志性动作：第一转肩 [CARD-GS:word/document.xml:P9846; T214.R006.C002]
- 结束标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9848; T214.R007.C002]
- 技术定义：球拍随转肩进入正手切削引拍方向，球拍从身体前方逐渐进入身体侧后方，拍头位置开始具备向高位引拍转换的趋势。 [CARD-GS:word/document.xml:P9850; T214.R008.C002]
- 原文关键项：球拍进入身体侧后方；球拍形成正手切削引拍方向；拍头位置开始上移；非持拍手辅助球拍控制；持拍手控制拍柄 [CARD-GS:word/document.xml:P9852; T214.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P9854; T214.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P9856; T214.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P9858; T214.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P9860; T214.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9862; T214.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9864; T214.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9866; T214.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9868; T214.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9870; T214.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P9872; T214.R019.C002]
- AI正向反馈：球拍进入正手切削引拍方向的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P9874; T214.R020.C002]
- AI改进反馈：球拍进入正手切削引拍方向存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P9876; T214.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M04 重心支撑

[CARD-GS:word/document.xml:P9879] 阶段定义：重心支撑建立，身体保持正手侧身姿态，非持拍手辅助球拍控制，持拍手控制拍柄，身体重心保持稳定并开始向击球方向移动。

[CARD-GS:word/document.xml:P9880] 开始：重心支撑建立    结束：球拍开始进入高位引拍

#### GS05-M04-01 建立重心支撑

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9891; T215.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9893; T215.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P9895; T215.R007.C002]
- 技术定义：双脚形成支撑，身体重心保持稳定。 [CARD-GS:word/document.xml:P9897; T215.R008.C002]
- 原文关键项：双脚形成稳定支撑；重心保持稳定；下肢保持控制；身体保持平衡；重心开始向击球方向移动 [CARD-GS:word/document.xml:P9899; T215.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P9901; T215.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P9903; T215.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9905; T215.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P9907; T215.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9909; T215.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9911; T215.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9913; T215.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9915; T215.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9917; T215.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9919; T215.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P9921; T215.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P9923; T215.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M04-02 保持正手侧身姿态

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9935; T216.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9937; T216.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P9939; T216.R007.C002]
- 技术定义：身体保持转肩后的正手侧身姿态。 [CARD-GS:word/document.xml:P9941; T216.R008.C002]
- 原文关键项：双肩保持转动姿态；胸廓保持侧向；骨盆保持稳定；身体姿态稳定；身体朝向保持可控 [CARD-GS:word/document.xml:P9943; T216.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P9945; T216.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P9947; T216.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P9949; T216.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P9951; T216.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9953; T216.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9955; T216.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P9957; T216.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P9959; T216.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P9961; T216.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P9963; T216.R019.C002]
- AI正向反馈：保持正手侧身姿态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P9965; T216.R020.C002]
- AI改进反馈：保持正手侧身姿态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P9967; T216.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M04-03 非持拍手辅助球拍控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P9979; T217.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P9981; T217.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P9983; T217.R007.C002]
- 技术定义：非持拍手辅助球拍控制，持拍手控制拍柄，维持正手切削引拍状态。 [CARD-GS:word/document.xml:P9985; T217.R008.C002]
- 原文关键项：非持拍手辅助球拍控制；持拍手控制拍柄；球拍保持稳定；拍面保持可控；双手协同控制球拍 [CARD-GS:word/document.xml:P9987; T217.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P9989; T217.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P9991; T217.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P9993; T217.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P9995; T217.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P9997; T217.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P9999; T217.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10001; T217.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10003; T217.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10005; T217.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P10007; T217.R019.C002]
- AI正向反馈：非持拍手辅助球拍控制的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10009; T217.R020.C002]
- AI改进反馈：非持拍手辅助球拍控制存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10011; T217.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M04-04 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10023; T218.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P10025; T218.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P10027; T218.R007.C002]
- 技术定义：重心支撑过程中持续观察来球。 [CARD-GS:word/document.xml:P10029; T218.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；观察球飞行轨迹；判断来球方向；判断来球速度 [CARD-GS:word/document.xml:P10031; T218.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10033; T218.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10035; T218.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P10037; T218.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10039; T218.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10041; T218.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10043; T218.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10045; T218.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10047; T218.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10049; T218.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P10051; T218.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P10053; T218.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P10055; T218.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M04-05 球拍开始进入高位引拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10067; T219.R005.C002]
- 开始标志性动作：重心支撑建立 [CARD-GS:word/document.xml:P10069; T219.R006.C002]
- 结束标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P10071; T219.R007.C002]
- 技术定义：球拍从身体侧后方开始向高位引拍位置移动，拍头位置开始上升。 [CARD-GS:word/document.xml:P10073; T219.R008.C002]
- 原文关键项：球拍向高位移动；拍头位置开始上升；球拍保持在身体侧后方；非持拍手辅助球拍控制；持拍手控制拍柄 [CARD-GS:word/document.xml:P10075; T219.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P10077; T219.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P10079; T219.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10081; T219.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P10083; T219.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10085; T219.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10087; T219.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10089; T219.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10091; T219.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10093; T219.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P10095; T219.R019.C002]
- AI正向反馈：球拍开始进入高位引拍的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10097; T219.R020.C002]
- AI改进反馈：球拍开始进入高位引拍存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10099; T219.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M05 高位引拍

[CARD-GS:word/document.xml:P10102] 阶段定义：球拍进入正手切削高位引拍位置，拍头高于预计击球点，非持拍手辅助球拍控制，持拍手控制拍柄，身体保持正手侧身支撑，建立正手切削的高位准备状态。

[CARD-GS:word/document.xml:P10103] 开始：球拍开始进入高位引拍    结束：拍面开始打开

#### GS05-M05-01 球拍进入正手高位引拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10114; T220.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P10116; T220.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10118; T220.R007.C002]
- 技术定义：球拍从身体侧后方继续上移，进入正手切削的高位引拍位置。 [CARD-GS:word/document.xml:P10120; T220.R008.C002]
- 原文关键项：球拍向高位移动；拍头位置上升；球拍保持在身体正手侧后方；持拍手控制拍柄；球拍运动保持连续 [CARD-GS:word/document.xml:P10122; T220.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P10124; T220.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P10126; T220.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10128; T220.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P10130; T220.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10132; T220.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10134; T220.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10136; T220.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10138; T220.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10140; T220.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P10142; T220.R019.C002]
- AI正向反馈：球拍进入正手高位引拍的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10144; T220.R020.C002]
- AI改进反馈：球拍进入正手高位引拍存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10146; T220.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M05-02 拍头高于预计击球点

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10158; T221.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P10160; T221.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10162; T221.R007.C002]
- 技术定义：拍头位置高于预计击球点，为后续向前下切削轨迹建立高度差。 [CARD-GS:word/document.xml:P10164; T221.R008.C002]
- 原文关键项：拍头高于预计击球点；球拍高度持续上升；来球高度持续被判断；高位引拍位置形成；切削挥拍高度差建立 [CARD-GS:word/document.xml:P10166; T221.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10168; T221.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10170; T221.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10172; T221.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10174; T221.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P10176; T221.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P10178; T221.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P10180; T221.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P10182; T221.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P10184; T221.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10186; T221.R019.C002]
- AI正向反馈：拍头高于预计击球点的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10188; T221.R020.C002]
- AI改进反馈：拍头高于预计击球点存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10190; T221.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M05-03 非持拍手辅助球拍控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10202; T222.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P10204; T222.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10206; T222.R007.C002]
- 技术定义：高位引拍过程中，非持拍手辅助球拍控制，持拍手控制拍柄，维持球拍姿态稳定。 [CARD-GS:word/document.xml:P10208; T222.R008.C002]
- 原文关键项：非持拍手辅助球拍控制；持拍手控制拍柄；双手协同控制球拍；拍面保持可控；球拍高位姿态稳定 [CARD-GS:word/document.xml:P10210; T222.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P10212; T222.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P10214; T222.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10216; T222.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P10218; T222.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10220; T222.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10222; T222.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10224; T222.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10226; T222.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10228; T222.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P10230; T222.R019.C002]
- AI正向反馈：非持拍手辅助球拍控制的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10232; T222.R020.C002]
- AI改进反馈：非持拍手辅助球拍控制存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10234; T222.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M05-04 身体保持正手侧身支撑

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10246; T223.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P10248; T223.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10250; T223.R007.C002]
- 技术定义：身体保持正手侧身姿态，重心保持稳定，为高位引拍提供身体支撑。 [CARD-GS:word/document.xml:P10252; T223.R008.C002]
- 原文关键项：身体保持正手侧身；胸廓保持转动姿态；骨盆保持稳定；重心保持稳定；下肢维持支撑 [CARD-GS:word/document.xml:P10254; T223.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P10256; T223.R010.C002]
- 所需点：J033/J034；J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P10258; T223.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P10260; T223.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P10262; T223.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10264; T223.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10266; T223.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10268; T223.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10270; T223.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10272; T223.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P10274; T223.R019.C002]
- AI正向反馈：身体保持正手侧身支撑完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P10276; T223.R020.C002]
- AI改进反馈：身体保持正手侧身支撑表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P10278; T223.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M05-05 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10290; T224.R005.C002]
- 开始标志性动作：球拍开始进入高位引拍 [CARD-GS:word/document.xml:P10292; T224.R006.C002]
- 结束标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10294; T224.R007.C002]
- 技术定义：高位引拍过程中持续观察来球。 [CARD-GS:word/document.xml:P10296; T224.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；判断来球高度；判断来球方向；判断切削击球时机 [CARD-GS:word/document.xml:P10298; T224.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10300; T224.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10302; T224.R011.C002]
- 当前状态：条件可评分 [CARD-GS:word/document.xml:P10304; T224.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10306; T224.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10308; T224.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10310; T224.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10312; T224.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10314; T224.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10316; T224.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P10318; T224.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P10320; T224.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P10322; T224.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M06 拍面打开

[CARD-GS:word/document.xml:P10325] 阶段定义：球拍保持正手高位引拍状态，拍面逐渐打开，持拍手控制拍柄，非持拍手自然离开球拍控制区域，球拍形成正手侧向前下方切削挥拍方向。

[CARD-GS:word/document.xml:P10326] 开始：拍面开始打开    结束：非持拍手离开球拍控制区域，球拍进入向前下切削方向

#### GS05-M06-01 拍面逐渐打开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10337; T225.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10339; T225.R006.C002]
- 结束标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10341; T225.R007.C002]
- 技术定义：持拍手控制拍柄，使拍面由正手高位引拍状态逐渐打开。 [CARD-GS:word/document.xml:P10343; T225.R008.C002]
- 原文关键项：拍面逐渐打开；持拍手控制拍柄；拍面角度保持可控；球拍姿态保持稳定；拍面方向与来球关系保持可识别 [CARD-GS:word/document.xml:P10345; T225.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10347; T225.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10349; T225.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10351; T225.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10353; T225.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10355; T225.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10357; T225.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10359; T225.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10361; T225.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10363; T225.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10365; T225.R019.C002]
- AI正向反馈：拍面逐渐打开的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10367; T225.R020.C002]
- AI改进反馈：拍面逐渐打开存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10369; T225.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M06-02 球拍保持正手高位

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10381; T226.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10383; T226.R006.C002]
- 结束标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10385; T226.R007.C002]
- 技术定义：球拍保持在正手高位引拍区域，拍头高于预计击球点。 [CARD-GS:word/document.xml:P10387; T226.R008.C002]
- 原文关键项：球拍保持正手高位；拍头高于预计击球点；球拍位于身体正手侧后方；球拍高度保持稳定；正手切削姿态保持 [CARD-GS:word/document.xml:P10389; T226.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10391; T226.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10393; T226.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10395; T226.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10397; T226.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10399; T226.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10401; T226.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10403; T226.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10405; T226.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10407; T226.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10409; T226.R019.C002]
- AI正向反馈：球拍保持正手高位的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10411; T226.R020.C002]
- AI改进反馈：球拍保持正手高位存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10413; T226.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M06-03 建立正手向前下切削方向

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10425; T227.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10427; T227.R006.C002]
- 结束标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10429; T227.R007.C002]
- 技术定义：球拍由正手高位状态形成向前下方运动方向，建立正手切削挥拍轨迹。 [CARD-GS:word/document.xml:P10431; T227.R008.C002]
- 原文关键项：球拍形成向前下方运动方向；拍头由高位进入下切方向；挥拍方向与来球轨迹形成切削关系；拍面保持打开状态；正手切削轨迹开始建立 [CARD-GS:word/document.xml:P10433; T227.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10435; T227.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10437; T227.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10439; T227.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10441; T227.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P10443; T227.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P10445; T227.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P10447; T227.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P10449; T227.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P10451; T227.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10453; T227.R019.C002]
- AI正向反馈：建立正手向前下切削方向完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P10455; T227.R020.C002]
- AI改进反馈：建立正手向前下切削方向表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P10457; T227.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M06-04 非持拍手自然离开球拍控制区域

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10469; T228.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10471; T228.R006.C002]
- 结束标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10473; T228.R007.C002]
- 技术定义：非持拍手从球拍控制区域自然离开，持拍手转为单手控制球拍。 [CARD-GS:word/document.xml:P10475; T228.R008.C002]
- 原文关键项：非持拍手离开球拍控制区域；持拍手单手控制拍柄；球拍姿态保持稳定；非持拍手自然展开；身体保持平衡 [CARD-GS:word/document.xml:P10477; T228.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P10479; T228.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P10481; T228.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P10483; T228.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P10485; T228.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10487; T228.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10489; T228.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10491; T228.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10493; T228.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10495; T228.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P10497; T228.R019.C002]
- AI正向反馈：非持拍手自然离开球拍控制区域的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10499; T228.R020.C002]
- AI改进反馈：非持拍手自然离开球拍控制区域存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10501; T228.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M06-05 持续盯球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10513; T229.R005.C002]
- 开始标志性动作：拍面开始打开 [CARD-GS:word/document.xml:P10515; T229.R006.C002]
- 结束标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10517; T229.R007.C002]
- 技术定义：拍面打开过程中持续观察来球位置、高度和飞行方向。 [CARD-GS:word/document.xml:P10519; T229.R008.C002]
- 原文关键项：头部保持稳定；持续跟踪来球；判断来球高度；判断来球方向；判断正手切削击球时机 [CARD-GS:word/document.xml:P10521; T229.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、头部位置与朝向稳定度；真实视线仅作近似、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10523; T229.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163；J004；J006/J007；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10525; T229.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10527; T229.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10529; T229.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10531; T229.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10533; T229.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10535; T229.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10537; T229.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10539; T229.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价；现有模型不能直接确认眼球真实注视方向 [CARD-GS:word/document.xml:P10541; T229.R019.C002]
- AI正向反馈：准备和动作过程中头部较稳定，观察动作保持较好。 [CARD-GS:word/document.xml:P10543; T229.R020.C002]
- AI改进反馈：观察过程中头部稳定性不足；当前系统只能近似判断头部朝向，训练时应持续跟踪来球。 [CARD-GS:word/document.xml:P10545; T229.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M07 正手切削挥拍

[CARD-GS:word/document.xml:P10548] 阶段定义：持拍手单手控制球拍沿正手侧向前下方轨迹挥拍，拍面保持打开状态，非持拍手自然张开维持身体平衡，球拍逐渐接近正手切削击球区域。

[CARD-GS:word/document.xml:P10549] 开始：非持拍手离开球拍控制区域，球拍进入向前下切削方向    结束：球拍轨迹与预测正手切削击球区域开始收敛

#### GS05-M07-01 正手切削挥拍

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10560; T230.R005.C002]
- 开始标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10562; T230.R006.C002]
- 结束标志性动作：球拍轨迹与预测正手切削击球区域开始收敛 [CARD-GS:word/document.xml:P10564; T230.R007.C002]
- 技术定义：持拍手单手控制球拍完成正手切削挥拍动作。 [CARD-GS:word/document.xml:P10566; T230.R008.C002]
- 原文关键项：持拍手单手控制球拍；球拍持续向击球区域运动；挥拍轨迹连续；拍柄控制稳定；球拍运动方向保持可控 [CARD-GS:word/document.xml:P10568; T230.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10570; T230.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10572; T230.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P10574; T230.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10576; T230.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10578; T230.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10580; T230.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10582; T230.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10584; T230.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10586; T230.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10588; T230.R019.C002]
- AI正向反馈：正手切削挥拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P10590; T230.R020.C002]
- AI改进反馈：正手切削挥拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P10592; T230.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M07-02 拍面保持打开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10604; T231.R005.C002]
- 开始标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10606; T231.R006.C002]
- 结束标志性动作：球拍轨迹与预测正手切削击球区域开始收敛 [CARD-GS:word/document.xml:P10608; T231.R007.C002]
- 技术定义：挥拍过程中拍面保持打开状态，形成正手切削击球所需的拍面角度。 [CARD-GS:word/document.xml:P10610; T231.R008.C002]
- 原文关键项：拍面保持打开；拍面角度保持稳定；持拍手控制拍柄；拍面方向与来球关系保持可识别；球拍姿态保持可控 [CARD-GS:word/document.xml:P10612; T231.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10614; T231.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10616; T231.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10618; T231.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10620; T231.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10622; T231.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10624; T231.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10626; T231.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10628; T231.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10630; T231.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10632; T231.R019.C002]
- AI正向反馈：拍面保持打开的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10634; T231.R020.C002]
- AI改进反馈：拍面保持打开存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10636; T231.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M07-03 球拍沿正手侧向前下方运动

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10648; T232.R005.C002]
- 开始标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10650; T232.R006.C002]
- 结束标志性动作：球拍轨迹与预测正手切削击球区域开始收敛 [CARD-GS:word/document.xml:P10652; T232.R007.C002]
- 技术定义：球拍由正手高位向前下方运动，形成正手切削挥拍轨迹。 [CARD-GS:word/document.xml:P10654; T232.R008.C002]
- 原文关键项：球拍由高位向前下方运动；拍头从高位下降；挥拍轨迹形成正手切削方向；球拍接近来球；向前下切削路径保持连续 [CARD-GS:word/document.xml:P10656; T232.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10658; T232.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10660; T232.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10662; T232.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10664; T232.R013.C002]
- A级：动作明显、连续，方向与时序合理，无明显失衡 [CARD-GS:word/document.xml:P10666; T232.R014.C002]
- B级：动作基本完成，幅度、速度或节奏略有不足 [CARD-GS:word/document.xml:P10668; T232.R015.C002]
- C级：观察到动作，但连续性、协调性或幅度一般 [CARD-GS:word/document.xml:P10670; T232.R016.C002]
- D级：动作幅度很小、方向异常或存在明显停顿 [CARD-GS:word/document.xml:P10672; T232.R017.C002]
- E级：未观察到该动作 [CARD-GS:word/document.xml:P10674; T232.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10676; T232.R019.C002]
- AI正向反馈：球拍沿正手侧向前下方运动的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10678; T232.R020.C002]
- AI改进反馈：球拍沿正手侧向前下方运动存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10680; T232.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M07-04 非持拍手自然张开

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10692; T233.R005.C002]
- 开始标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10694; T233.R006.C002]
- 结束标志性动作：球拍轨迹与预测正手切削击球区域开始收敛 [CARD-GS:word/document.xml:P10696; T233.R007.C002]
- 技术定义：非持拍手离开球拍控制区域后自然张开，维持身体平衡和上半身协调。 [CARD-GS:word/document.xml:P10698; T233.R008.C002]
- 原文关键项：非持拍手自然张开；非持拍手与球拍控制区域分离；上肢保持协调；身体保持平衡；动作自然连续 [CARD-GS:word/document.xml:P10700; T233.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P10702; T233.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P10704; T233.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P10706; T233.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P10708; T233.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10710; T233.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10712; T233.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10714; T233.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10716; T233.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10718; T233.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P10720; T233.R019.C002]
- AI正向反馈：非持拍手自然张开完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P10722; T233.R020.C002]
- AI改进反馈：非持拍手自然张开表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P10724; T233.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M07-05 球拍接近正手切削击球区域

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10736; T234.R005.C002]
- 开始标志性动作：非持拍手离开球拍控制区域，球拍进入向前下切削方向 [CARD-GS:word/document.xml:P10738; T234.R006.C002]
- 结束标志性动作：球拍轨迹与预测正手切削击球区域开始收敛 [CARD-GS:word/document.xml:P10740; T234.R007.C002]
- 技术定义：球拍沿正手切削轨迹逐渐接近预计击球区域，为正手切削击球建立触球前状态。 [CARD-GS:word/document.xml:P10742; T234.R008.C002]
- 原文关键项：球拍接近预计击球区域；拍头位置接近来球；拍面保持打开；球拍轨迹与来球轨迹开始收敛；正手切削击球时机接近形成 [CARD-GS:word/document.xml:P10744; T234.R009.C002]
- 当前识别点：当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10746; T234.R010.C002]
- 所需点：RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10748; T234.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10750; T234.R012.C002]
- 计算方式：球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10752; T234.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P10754; T234.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P10756; T234.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P10758; T234.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P10760; T234.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P10762; T234.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10764; T234.R019.C002]
- AI正向反馈：球拍接近正手切削击球区域的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10766; T234.R020.C002]
- AI改进反馈：球拍接近正手切削击球区域存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10768; T234.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M08 切削击球

[CARD-GS:word/document.xml:P10771] 阶段定义：球拍与球接触，持拍手单手控制球拍完成正手切削击球，拍面保持打开状态，球拍沿正手侧向前下方轨迹摩擦来球，形成下旋。

[CARD-GS:word/document.xml:P10772] 开始：球拍触球    结束：球离拍并产生下旋

#### GS05-M08-01 正手切削触球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10783; T235.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P10785; T235.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P10787; T235.R007.C002]
- 技术定义：持拍手单手控制球拍与球接触，完成正手切削触球动作。 [CARD-GS:word/document.xml:P10789; T235.R008.C002]
- 原文关键项：持拍手单手控制球拍；球拍甜区接触来球；头部保持稳定；观察触球区域；触球动作连续 [CARD-GS:word/document.xml:P10791; T235.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10793; T235.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10795; T235.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P10797; T235.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10799; T235.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P10801; T235.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P10803; T235.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P10805; T235.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P10807; T235.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P10809; T235.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10811; T235.R019.C002]
- AI正向反馈：正手切削触球在活动球与球拍专项事件可用后可进行正式评价。 [CARD-GS:word/document.xml:P10813; T235.R020.C002]
- AI改进反馈：正手切削触球当前不应由AI直接猜测；需等待稳定球轨迹、球拍关键点和触球事件后再给技术结论。 [CARD-GS:word/document.xml:P10815; T235.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M08-02 拍面保持打开

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10827; T236.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P10829; T236.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P10831; T236.R007.C002]
- 技术定义：击球瞬间拍面保持打开状态，形成正手切削击球所需的拍面角度。 [CARD-GS:word/document.xml:P10833; T236.R008.C002]
- 原文关键项：拍面保持打开；拍面角度稳定；持拍手控制拍柄；球拍姿态可控；拍面方向与来球形成切削关系 [CARD-GS:word/document.xml:P10835; T236.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10837; T236.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10839; T236.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10841; T236.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10843; T236.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10845; T236.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10847; T236.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10849; T236.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10851; T236.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10853; T236.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10855; T236.R019.C002]
- AI正向反馈：拍面保持打开的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10857; T236.R020.C002]
- AI改进反馈：拍面保持打开存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10859; T236.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M08-03 球拍向前下摩擦来球

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10871; T237.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P10873; T237.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P10875; T237.R007.C002]
- 技术定义：球拍沿正手侧向前下方轨迹运动，与来球形成切削摩擦。 [CARD-GS:word/document.xml:P10877; T237.R008.C002]
- 原文关键项：球拍向前下方运动；拍头从高位向下运动；球拍与来球形成摩擦；挥拍轨迹保持连续；正手切削路径保持稳定 [CARD-GS:word/document.xml:P10879; T237.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10881; T237.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10883; T237.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P10885; T237.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10887; T237.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10889; T237.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10891; T237.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10893; T237.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10895; T237.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10897; T237.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10899; T237.R019.C002]
- AI正向反馈：球拍向前下摩擦来球的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P10901; T237.R020.C002]
- AI改进反馈：球拍向前下摩擦来球存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P10903; T237.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M08-04 球产生下旋

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10915; T238.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P10917; T238.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P10919; T238.R007.C002]
- 技术定义：球离开拍面后形成下旋飞行状态。 [CARD-GS:word/document.xml:P10921; T238.R008.C002]
- 原文关键项：球离开拍面；球产生下旋；球飞行方向形成；球速发生变化；旋转轴可被识别 [CARD-GS:word/document.xml:P10923; T238.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P10925; T238.R010.C002]
- 所需点：J033/J034；J071/J072；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P10927; T238.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P10929; T238.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P10931; T238.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P10933; T238.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P10935; T238.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P10937; T238.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P10939; T238.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P10941; T238.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P10943; T238.R019.C002]
- AI正向反馈：球产生下旋完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P10945; T238.R020.C002]
- AI改进反馈：球产生下旋表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P10947; T238.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M08-05 非持拍手张开并保持身体稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P10959; T239.R005.C002]
- 开始标志性动作：球拍触球 [CARD-GS:word/document.xml:P10961; T239.R006.C002]
- 结束标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P10963; T239.R007.C002]
- 技术定义：非持拍手自然张开，身体保持稳定支撑，完成正手切削击球瞬间的身体控制。 [CARD-GS:word/document.xml:P10965; T239.R008.C002]
- 原文关键项：非持拍手自然张开；身体保持稳定；胸廓保持控制；骨盆保持稳定；重心保持可控 [CARD-GS:word/document.xml:P10967; T239.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P10969; T239.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163 [CARD-GS:word/document.xml:P10971; T239.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P10973; T239.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P10975; T239.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P10977; T239.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P10979; T239.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P10981; T239.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P10983; T239.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P10985; T239.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P10987; T239.R019.C002]
- AI正向反馈：非持拍手张开并保持身体稳定完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P10989; T239.R020.C002]
- AI改进反馈：非持拍手张开并保持身体稳定表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P10991; T239.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M09 切削收拍

[CARD-GS:word/document.xml:P10994] 阶段定义：球离拍后，持拍手继续控制球拍沿正手侧向前下方轨迹完成收拍，拍头位置下降，拍面保持可控，非持拍手自然张开，身体保持稳定，完成正手低位切削收拍动作。

[CARD-GS:word/document.xml:P10995] 开始：球离拍并产生下旋    结束：正手低位切削收拍完成

#### GS05-M09-01 持拍手继续完成正手切削挥拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11006; T240.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P11008; T240.R006.C002]
- 结束标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11010; T240.R007.C002]
- 技术定义：球离拍后，持拍手继续控制球拍完成正手切削挥拍轨迹。 [CARD-GS:word/document.xml:P11012; T240.R008.C002]
- 原文关键项：持拍手继续控制球拍；球拍继续沿正手切削方向运动；挥拍轨迹连续；拍柄控制稳定；球拍速度逐渐降低 [CARD-GS:word/document.xml:P11014; T240.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P11016; T240.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P11018; T240.R011.C002]
- 当前状态：暂不可评分 [CARD-GS:word/document.xml:P11020; T240.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P11022; T240.R013.C002]
- A级：专项视觉数据可用后：关键行为完整、稳定、时序合理 [CARD-GS:word/document.xml:P11024; T240.R014.C002]
- B级：专项视觉数据可用后：行为基本完成，存在轻微幅度或节奏不足 [CARD-GS:word/document.xml:P11026; T240.R015.C002]
- C级：专项视觉数据可用后：观察到主要行为，但稳定性/协调性一般 [CARD-GS:word/document.xml:P11028; T240.R016.C002]
- D级：专项视觉数据可用后：行为明显不足、方向异常或存在停顿 [CARD-GS:word/document.xml:P11030; T240.R017.C002]
- E级：专项视觉数据可用后：未观察到该关键行为 [CARD-GS:word/document.xml:P11032; T240.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P11034; T240.R019.C002]
- AI正向反馈：持拍手继续完成正手切削挥拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P11036; T240.R020.C002]
- AI改进反馈：持拍手继续完成正手切削挥拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P11038; T240.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M09-02 拍头继续下降

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11050; T241.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P11052; T241.R006.C002]
- 结束标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11054; T241.R007.C002]
- 技术定义：球拍在收拍过程中继续向低位运动，拍头位置下降。 [CARD-GS:word/document.xml:P11056; T241.R008.C002]
- 原文关键项：拍头继续下降；球拍由高位进入低位；球拍运动方向保持可识别；收拍位置低于击球前高位引拍；正手切削收拍轨迹形成 [CARD-GS:word/document.xml:P11058; T241.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系 [CARD-GS:word/document.xml:P11060; T241.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P11062; T241.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P11064; T241.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P11066; T241.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P11068; T241.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P11070; T241.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P11072; T241.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P11074; T241.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P11076; T241.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P11078; T241.R019.C002]
- AI正向反馈：拍头继续下降的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P11080; T241.R020.C002]
- AI改进反馈：拍头继续下降存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P11082; T241.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M09-03 拍面保持可控

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11094; T242.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P11096; T242.R006.C002]
- 结束标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11098; T242.R007.C002]
- 技术定义：收拍过程中拍面保持可控状态，避免球拍姿态失控。 [CARD-GS:word/document.xml:P11100; T242.R008.C002]
- 原文关键项：拍面保持可控；拍面角度逐渐恢复；持拍手控制拍柄；球拍姿态保持稳定；拍面方向变化可识别 [CARD-GS:word/document.xml:P11102; T242.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P11104; T242.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P11106; T242.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P11108; T242.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P11110; T242.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P11112; T242.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P11114; T242.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P11116; T242.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P11118; T242.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P11120; T242.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P11122; T242.R019.C002]
- AI正向反馈：拍面保持可控的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P11124; T242.R020.C002]
- AI改进反馈：拍面保持可控存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P11126; T242.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M09-04 非持拍手自然张开

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11138; T243.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P11140; T243.R006.C002]
- 结束标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11142; T243.R007.C002]
- 技术定义：非持拍手保持自然张开状态，维持身体平衡和上半身协调。 [CARD-GS:word/document.xml:P11144; T243.R008.C002]
- 原文关键项：非持拍手自然张开；非持拍手与球拍控制区域保持分离；上肢保持协调；身体保持平衡；身体姿态稳定 [CARD-GS:word/document.xml:P11146; T243.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点 [CARD-GS:word/document.xml:P11148; T243.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P11150; T243.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P11152; T243.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P11154; T243.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P11156; T243.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P11158; T243.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P11160; T243.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P11162; T243.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P11164; T243.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P11166; T243.R019.C002]
- AI正向反馈：非持拍手自然张开完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P11168; T243.R020.C002]
- AI改进反馈：非持拍手自然张开表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P11170; T243.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M09-05 身体保持稳定并完成正手低位收拍

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11182; T244.R005.C002]
- 开始标志性动作：球离拍并产生下旋 [CARD-GS:word/document.xml:P11184; T244.R006.C002]
- 结束标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11186; T244.R007.C002]
- 技术定义：身体保持稳定控制，持拍手完成正手低位切削收拍动作。 [CARD-GS:word/document.xml:P11188; T244.R008.C002]
- 原文关键项：身体保持稳定；胸廓保持控制；骨盆保持稳定；重心保持可控；正手低位切削收拍完成 [CARD-GS:word/document.xml:P11190; T244.R009.C002]
- 当前识别点：双肩/双髋二维角度变化、肩髋中心轨迹、动作时序、双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移 [CARD-GS:word/document.xml:P11192; T244.R010.C002]
- 所需点：J033/J034；J071/J072；J101/J121；J103/J123；J141/J161；J143/J163 [CARD-GS:word/document.xml:P11194; T244.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P11196; T244.R012.C002]
- 计算方式：肩线/髋线二维角度及角速度 + 腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P11198; T244.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P11200; T244.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P11202; T244.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P11204; T244.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P11206; T244.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P11208; T244.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P11210; T244.R019.C002]
- AI正向反馈：身体保持稳定并完成正手低位收拍完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P11212; T244.R020.C002]
- AI改进反馈：身体保持稳定并完成正手低位收拍表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P11214; T244.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

### GS05-M10 恢复准备

[CARD-GS:word/document.xml:P11217] 阶段定义：完成正手低位切削收拍后，身体姿态逐渐恢复，重心重新稳定，非持拍手重新接触球拍控制区域，身体与球拍进入通用准备状态。

[CARD-GS:word/document.xml:P11218] 开始：正手低位切削收拍完成    结束：身体恢复稳定通用准备状态

#### GS05-M10-01 非持拍手重新接触球拍控制区域

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11229; T245.R005.C002]
- 开始标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11231; T245.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P11233; T245.R007.C002]
- 技术定义：非持拍手从自然张开状态重新靠近球拍，并重新接触球拍控制区域。 [CARD-GS:word/document.xml:P11235; T245.R008.C002]
- 原文关键项：非持拍手靠近球拍控制区域；非持拍手重新接触 RK013；双手重新建立球拍控制；球拍姿态恢复稳定；动作自然连续 [CARD-GS:word/document.xml:P11237; T245.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P11239; T245.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P11241; T245.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P11243; T245.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P11245; T245.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P11247; T245.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P11249; T245.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P11251; T245.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P11253; T245.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P11255; T245.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P11257; T245.R019.C002]
- AI正向反馈：非持拍手重新接触球拍控制区域的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P11259; T245.R020.C002]
- AI改进反馈：非持拍手重新接触球拍控制区域存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P11261; T245.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M10-02 持拍手恢复拍柄控制

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11273; T246.R005.C002]
- 开始标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11275; T246.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P11277; T246.R007.C002]
- 技术定义：持拍手保持拍柄控制，球拍运动速度降低，球拍逐渐脱离正手低位收拍状态。 [CARD-GS:word/document.xml:P11279; T246.R008.C002]
- 原文关键项：持拍手控制拍柄；球拍运动速度降低；拍面恢复可控；球拍姿态恢复稳定；双手控制关系重新建立 [CARD-GS:word/document.xml:P11281; T246.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、活动球轨迹稳定后可增加球—身体/球—球拍空间与时序关系、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P11283; T246.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后）；BALL活动球轨迹（训练后） [CARD-GS:word/document.xml:P11285; T246.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P11287; T246.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） + 球轨迹 + 球拍/身体空间关系 + 事件时序（活动球稳定后） [CARD-GS:word/document.xml:P11289; T246.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P11291; T246.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P11293; T246.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P11295; T246.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P11297; T246.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P11299; T246.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价；活动球轨迹/击球事件不可用时，球相关部分不评价 [CARD-GS:word/document.xml:P11301; T246.R019.C002]
- AI正向反馈：持拍手恢复拍柄控制完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P11303; T246.R020.C002]
- AI改进反馈：持拍手恢复拍柄控制表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P11305; T246.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M10-03 球拍从正手低位回到身体前方

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11317; T247.R005.C002]
- 开始标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11319; T247.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P11321; T247.R007.C002]
- 技术定义：球拍从正手低位切削收拍位置逐渐回到身体前方，恢复准备状态下的球拍位置。 [CARD-GS:word/document.xml:P11323; T247.R008.C002]
- 原文关键项：球拍从正手低位抬起；球拍回到身体前方；拍头高度恢复；球拍与身体距离恢复；球拍姿态保持可控 [CARD-GS:word/document.xml:P11325; T247.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P11327; T247.R010.C002]
- 所需点：J101/J121；J103/J123；RK专项关键点（训练后） [CARD-GS:word/document.xml:P11329; T247.R011.C002]
- 当前状态：部分可评分 [CARD-GS:word/document.xml:P11331; T247.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P11333; T247.R013.C002]
- A级：关键行为完整、连续、稳定，符合阶段技术目的 [CARD-GS:word/document.xml:P11335; T247.R014.C002]
- B级：关键行为基本完成，仅有轻微幅度或节奏不足 [CARD-GS:word/document.xml:P11337; T247.R015.C002]
- C级：主要行为已经出现，但完整性/协调性一般 [CARD-GS:word/document.xml:P11339; T247.R016.C002]
- D级：关键行为明显不足或出现明显停顿/失衡 [CARD-GS:word/document.xml:P11341; T247.R017.C002]
- E级：未观察到该关键行为 [CARD-GS:word/document.xml:P11343; T247.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P11345; T247.R019.C002]
- AI正向反馈：球拍从正手低位回到身体前方的可观测部分较完整；球拍专项关键点可用后可进一步确认细节。 [CARD-GS:word/document.xml:P11347; T247.R020.C002]
- AI改进反馈：球拍从正手低位回到身体前方存在不足；当前仅能评价人体/球拍框可观测部分，拍面和拍头细节需专项模型确认。 [CARD-GS:word/document.xml:P11349; T247.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M10-04 重心恢复稳定

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11361; T248.R005.C002]
- 开始标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11363; T248.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P11365; T248.R007.C002]
- 技术定义：身体重心重新稳定，双脚恢复稳定支撑。 [CARD-GS:word/document.xml:P11367; T248.R008.C002]
- 原文关键项：重心恢复稳定；双脚重新形成支撑；身体保持平衡；重心位于双脚之间；下肢稳定控制 [CARD-GS:word/document.xml:P11369; T248.R009.C002]
- 当前识别点：髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P11371; T248.R010.C002]
- 所需点：J071/J072；J141/J161；J143/J163 [CARD-GS:word/document.xml:P11373; T248.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P11375; T248.R012.C002]
- 计算方式：髋中心位移/速度 + 膝踝角度变化 [CARD-GS:word/document.xml:P11377; T248.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P11379; T248.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P11381; T248.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P11383; T248.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P11385; T248.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P11387; T248.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70% [CARD-GS:word/document.xml:P11389; T248.R019.C002]
- AI正向反馈：身体中心转移连续，支撑和平衡保持较好。 [CARD-GS:word/document.xml:P11391; T248.R020.C002]
- AI改进反馈：身体中心转移不足或支撑不稳，建议先建立稳定下肢支撑再衔接下一阶段。 [CARD-GS:word/document.xml:P11393; T248.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；event_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

#### GS05-M10-05 建立通用准备状态

待核验源冲突：GS-CONFLICT-05。

- 来源/复用：原文直接提取 [CARD-GS:word/document.xml:P11405; T249.R005.C002]
- 开始标志性动作：正手低位切削收拍完成 [CARD-GS:word/document.xml:P11407; T249.R006.C002]
- 结束标志性动作：身体恢复稳定通用准备状态 [CARD-GS:word/document.xml:P11409; T249.R007.C002]
- 技术定义：完成正手切削收拍后的身体与球拍恢复，身体进入可继续启动、移动或击球的中性准备状态。 [CARD-GS:word/document.xml:P11411; T249.R008.C002]
- 原文关键项：球拍回到身体前方；双手重新建立球拍控制；身体恢复稳定姿态；重心恢复稳定；不限定下一拍技术类型 [CARD-GS:word/document.xml:P11413; T249.R009.C002]
- 当前识别点：双肘/双腕轨迹、速度、相对位置与连续性、髋中心（视觉重心代理）、膝踝伸展/屈曲、人体中心位移、当前可用球拍检测框；精细拍面/拍头状态需专项球拍关键点、动作结束后的身体中心速度、支撑稳定度与准备姿态恢复 [CARD-GS:word/document.xml:P11415; T249.R010.C002]
- 所需点：J101/J121；J103/J123；J071/J072；J141/J161；J143/J163；RK专项关键点（训练后） [CARD-GS:word/document.xml:P11417; T249.R011.C002]
- 当前状态：可评分 [CARD-GS:word/document.xml:P11419; T249.R012.C002]
- 计算方式：腕/肘轨迹、速度与相对距离 + 髋中心位移/速度 + 膝踝角度变化 + 球拍关键点轨迹/拍轴/拍面二维投影（专项模型后） [CARD-GS:word/document.xml:P11421; T249.R013.C002]
- A级：动作稳定、连续，身体/球拍控制良好，无明显失衡 [CARD-GS:word/document.xml:P11423; T249.R014.C002]
- B级：基本稳定，存在轻微晃动或节奏波动 [CARD-GS:word/document.xml:P11425; T249.R015.C002]
- C级：主要动作完成，但稳定性或控制性一般 [CARD-GS:word/document.xml:P11427; T249.R016.C002]
- D级：明显不稳定、出现较大偏移或动作停顿 [CARD-GS:word/document.xml:P11429; T249.R017.C002]
- E级：未形成该稳定/控制行为 [CARD-GS:word/document.xml:P11431; T249.R018.C002]
- 不可评价：目标球员 track_id 不稳定；核心人体关键点有效帧低于70%；该指标依赖的球拍专项关键点不可用时，球拍细节部分不评价 [CARD-GS:word/document.xml:P11433; T249.R019.C002]
- AI正向反馈：建立通用准备状态完成较完整，动作连续性和身体控制较好。 [CARD-GS:word/document.xml:P11435; T249.R020.C002]
- AI改进反馈：建立通用准备状态表现不足，建议优先按照该阶段原文关键项逐项调整。 [CARD-GS:word/document.xml:P11437; T249.R021.C002]
- 单位/标量评分方向/数值分档/权重/100分映射/扣分/聚合：原文未给出。
- 实现：原文字段一致；dependency_implementation_required；事件定位未实现；GS正式特征要求未列入当前13项requirements；需教练标定。

## 前端可以诚实展示什么

- 显示来源文件、SHA256、指标ID及段落/表格坐标；可核验原文与当前解释。
- 将技术表现、证据可见率、识别置信度和工程就绪度分开命名；任何100分参考就绪度都不得叫技术总分。
- 每项显示已观测事实、测量值及单位/窗口/代理性质、缺测原因、未实现/待标定状态；没有值时显示空值或不可评价，不显示0或E。
- 来源标注“可评分”只是原文件判断，不代表本视频已定位该事件，也不代表已有数值评级。
- 正式评分需额外展示阈值/模型版本、适用机位/动作、教练标定来源、质量门槛和聚合规则来源。现有GS证据不足以提供这些字段。
- A—E原文可作为人工审阅参考文本呈现；自动匹配等级必须有可信标定，不可由覆盖率决定。
- “扣了几分”当前应显示未定义扣分机制；反馈使用可见动作与证据，不能把缺测写成技术错误。
- 源冲突三卡GS01-M10-04、GS02-M01-01/02显示规则待核定；跨版本复用差异保持可见。
- 当前可诚实展示5类动作的定义、50阶段/248指标原文目录、来源条件、待实现/待标定进度及真实可用的证据。

结论：不能从这些规则证明任何视频应得100分、应扣若干分或应获得某自动技术等级；不能以不可见/未实现代替低分。
