# FS 步伐规则逐项核验（2026-10-04）

核验10个事件、50个正式指标。两份 metric-cards 的15个卡片字段逐项匹配源文（包括5级文字），共1500次字段对应无差异。指标原文完整字段及每个值的段落、表格单元格、XPath和稳定ID见 fs-audit.json。工程定义使用 implementation-snapshot 固定版本；额外特征定义只读并单独记录SHA256。

原文状态：可评分20、部分可评分13、条件可评分13、暂不可评分4。34项明确70%有效帧质量条件；50项均没有数字等级界线、100分映射、扣分、权重或聚合公式。当前v2与requirements只有13项F2，无F4；其余37项测量计划仍需事件或依赖实现。

## 结论与处理原则

可以诚实展示来源、定性等级描述、有效证据、带单位的代理测量和待标定原因。仅凭这些Word不能输出可核验的百分制/扣分，不能自动赋予A～E。F2是测量能力，不是已通过教练验证的评分。低于70%是观测质量门槛，不是C级分界。E级“未观察到行为”也不能覆盖遮挡、身份不稳或片段不完整的不可评价。

## 一致性差异

|编号|状态|问题|建议|
|---|---|---|---|
|FS-SOURCE-VERSION|不一致|文件名第二版与正文第一版冲突；文件名 FS步伐事件评分指标卡_第二版.docx；正文标题写第一版。不能靠文件名推断已批准的版本优先级。|以本次 SHA256 固定原件；请规则维护者确认正式版本。|
|FS-SOURCE-START|不一致|FS01起点同文档存在两种表述；总表为重心向下，微蹲；事件详细标题为重心预加载开始。当前 cards 使用后者。大体同义，但没有精确边界算法。|保留两处；详细卡用于可追溯描述，总表保留别名，边界需人工定义/标注。|
|FS-NUMERIC-GRADE|需标定|原文没有百分制、数字等级边界、权重或扣分公式；前端演示使用40/25/20/15四维权重，GS/FS 70/30，90/80/70/60分级，指标/事件等权聚合，scenario.baseScore 加确定性 hash 扰动；均不是 FS 原文规则。real 分支已置 score/grade 为 null。|仅作为清楚标注的演示；正式输出缺少教练标定时保持 null。不能把 100-score 解释成逐项扣分。|
|FS-E-GRADE-NOT-MISSING|一致|E级未观察到行为不能代替不可评价；源文分别列 E级与不可评价；Python 在质量失败/特征无效时 unavailable/grade=null，缺标定 calibration_required/grade=null；plans 明确 null_not_zero。|前端保留独立状态；只有已满足观测条件且完成标定后才能给 E。|
|FS-COVERAGE-GATE|不一致|源70%指标关节帧门槛不能被统一证据分替代；34项明确低于70%不可评价，部位各异。engine 用依赖覆盖率<0.65及混合evidence>=0.72；特征库 MIN_VALID_FRACTION=0.50 是F2测量可用门槛。并非同一统计口径；已读评分路径未见逐指标执行源关节70%规则。|逐指标记录时间窗、所需关节和有效帧分母；F2测量允许保留但不得称满足源评分门槛。正式评分前执行原文门槛，并核定70%边界与有效帧定义。|
|FS-PLANS-STALE|不一致|测量计划的完成状态/版本与子条款不同步；13项 plans.runtime_status=implemented_f2_calibration_required，但内部若干条款仍写 feature_implementation_required，events 模型版本仍 pose-motion-bout-v0.1.0，plan引用feasibility pose-wave-2026-08-21.8。旧feasibility只有6项，v2/requirements为13项。|建立单一生成源和版本同步检查；逐条款确认覆盖，不把F2标签解读为已完整实现原文。|
|FS-OPTIONAL-DEPS|不一致|可选依赖在计划层缺少降级分支；FS01-M01无球时允许只评价头部稳定；FS10-M02场地标定建议；FS10-M05场地信息可选。plans 只给 dependencies 集合与单一 dependency_implementation_required，不能表达降级后的可测范围。engine有文本可选识别但覆盖率不是测量。|显式区分必需/增强/替代证据和 full/partial scope，不以可选球/场地缺测扣分。|
|FS01-M02-DELTA|不一致|预加载源变化量与当前统计量不同；源要求髋中心高度下降量、双膝夹角变化和身体中心速度连续性；当前必需特征为髋高median、膝屈曲peak、站宽、支撑比，不等于下降量/变化量/连续性完整实现。|补明前后窗口并计算差值/连续性；保留现有测量为代理，不给原文完整完成结论。|
|FS01-M04-SCALE|不一致|踝距归一化分母与原文不同；原文为落地后踝距/髋宽比例；当前 post_slowdown_stance_width_body 为脚参考点间距/body scale，且落地只是脚减速代理。|增加 ankle_distance/hip_width 的独立特征或经批准变更规则；明确脚减速不是真实触地。|
|FS01-M05-CROSS-EVENT|未实现|FS02启动关联与时间间隔未在必需契约中完整表达；源规定FS02启动未识别不可评价，并要计算到FS02的间隔。当前契约为FS01.redistribution/FS01.initiation及4个姿态特征；没有要求独立FS02事件ID关联或跨事件间隔特征。|添加同球员FS02关联及interval；未关联时仅测重心/支撑，完整指标 unavailable。|
|FS02-M02-TARGET|不一致|来球方向与目标方向的语义需明确；原卡名称写重心向来球方向转换，而技术定义写目标方向；feasibility沿用目标方向并加入外部target_direction与alignment。受控训练目标可用于该扩展，但不等于原视频观测的来球方向。原卡requiredPoints只有pose，与附录完整方向评分条件亦需协调。|保留原名及测量子范围；将外部目标来源/坐标系独立展示；规则维护者需明确目标是否必须等于来球方向，完整一致性须满足相应外部证据。|
|FS09-M05-CROSS-EVENT|未实现|稳定控制的后续事件连续性未完整接入；必需特征/语义含稳定时长、双支撑代理、减速等，没有FS10/FS02关联要求；FS10在plans未实现。不能据内部减速/稳定推断已连续进入下一事件。|增加后续同球员FS10或FS02关联，否则输出可测子项而非整项完成。|
|FS-PROXY-LIMITS|一致|视觉中心、离地/落地、发力均为代理；特征定义明确proxy/not contact/not force；源同样禁止把髋中心当真实生物力学重心、踝速度当真实足底接触。|文案统一使用视觉人体中心、脚部抬升/减速代理；不能输出真实发力牛顿值、真实注视或真实承重。|

## 事件边界及技术目录对应

|事件|详细起点|详细终点|技术目录ID|原文位置|
|---|---|---|---|---|
|FS01 分腿垫步|开始标志性动作：重心预加载开始|结束标志性动作：双脚落地完成垫步|split_step|CARD-FS:word/document.xml:P0061|
|FS02 第一步启动|开始标志性动作：双脚落地完成垫步|结束标志性动作：第一步落地并建立移动方向|first_step|CARD-FS:word/document.xml:P0239|
|FS03 交叉步移动|开始标志性动作：第一步落地并建立移动方向|结束标志性动作：交叉步移动完成并接近目标击球区域|crossover_step|CARD-FS:word/document.xml:P0417|
|FS04 并步移动|开始标志性动作：移动方向建立并进入短距离横向调整|结束标志性动作：并步移动完成并接近目标击球区域|shuffle_step|CARD-FS:word/document.xml:P0595|
|FS05 小碎步调整|开始标志性动作：身体接近目标击球区域|结束标志性动作：击球支撑位置建立|adjustment_steps|CARD-FS:word/document.xml:P0773|
|FS06 开放式支撑|开始标志性动作：击球支撑位置建立|结束标志性动作：开放式击球支撑建立|open_stance|CARD-FS:word/document.xml:P0951|
|FS07 闭合式支撑|开始标志性动作：击球支撑位置建立|结束标志性动作：闭合式击球支撑建立|closed_stance|CARD-FS:word/document.xml:P1129|
|FS08 跨步支撑|开始标志性动作：击球支撑位置建立但距离不足|结束标志性动作：跨步击球支撑建立|lunge_support|CARD-FS:word/document.xml:P1307|
|FS09 制动急停/稳定|开始标志性动作：身体惯性需要被控制|结束标志性动作：身体惯性降低并重新建立稳定支撑|braking_stabilization|CARD-FS:word/document.xml:P1485|
|FS10 击球后回位|开始标志性动作：身体惯性降低并重新建立稳定支撑|结束标志性动作：回位至合理场地准备位置|recovery_positioning|CARD-FS:word/document.xml:P1663|

## 全部正式指标

每项原文“+”是特征组合描述，未规定系数、标准化和求和公式；不能直接将量纲不同的量相加作为分数。代码已有单位保存在JSON feature_definitions；源文并未统一时间、角度和空间单位。以下按正式源指标无遗漏列出。

### FS01-M01 观察对方击球

- 原文定位：CARD-FS:word/document.xml:P0068；表格 T002。
- 定义：分腿垫步前保持头部稳定并持续观察对方击球与来球方向，为垫步时机和启动方向建立视觉准备。
- 原文状态：部分可评分。项目状态：dependency_implementation_required。
- 当前识别点：头部稳定度、头部朝向变化、对方击球后球候选方向（如可用）
- 所需点：J004（鼻）、J006/J007（耳）；可选 BALL-010、BALL-020、BALL-025
- 计算描述（CARD-FS:word/document.xml:P0080）：头部中心速度与角度波动 + 头部朝向相对来球方向的一致性；球轨迹不可用时仅评价头部稳定
- 不可评价/范围限制（CARD-FS:word/document.xml:P0092）：头部有效帧低于70%，或目标球员身份不稳定；“是否真正盯球”不可由现有2D Pose直接确认
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0082）：观察准备明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0084）：观察准备基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0086）：观察到观察准备，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0088）：观察准备很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0090）：未观察到观察准备
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。
- 专项差异：FS-SOURCE-START、FS-OPTIONAL-DEPS。

### FS01-M02 重心预加载

- 原文定位：CARD-FS:word/document.xml:P0103；表格 T003。
- 定义：身体中心下压，双膝和踝关节进入弹性准备状态，为垫步离地和快速启动蓄力。
- 原文状态：可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：髋部中心下移、双膝屈曲变化、双踝相对稳定、躯干倾斜变化
- 所需点：J071/J072（髋）、J141/J161（膝）、J143/J163（踝）、J033/J034（肩）
- 计算描述（CARD-FS:word/document.xml:P0115）：髋中心归一化高度下降量 + 双膝夹角变化 + 身体中心速度连续性
- 不可评价/范围限制（CARD-FS:word/document.xml:P0127）：双髋或双膝有效帧低于70%，或机位导致垂直方向严重失真
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0117）：重心预加载明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0119）：重心预加载基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0121）：观察到重心预加载，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0123）：重心预加载很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0125）：未观察到重心预加载
- 已有必需特征：hip_center_y_body [body]；left_knee_flexion_deg [deg]；right_knee_flexion_deg [deg]；stance_width_body [body]；hip_center_relative_to_ankle_support [ratio]。需标定；不是原文阈值。
- 专项差异：FS-SOURCE-START、FS01-M02-DELTA。

### FS01-M03 双脚轻微离地

- 原文定位：CARD-FS:word/document.xml:P0138；表格 T004。
- 定义：双脚短暂离开地面并保持身体稳定，形成分腿垫步的弹跳阶段。
- 原文状态：部分可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：双踝同步上移、双膝伸展、人体中心短暂上移、离地持续时间
- 所需点：J141/J161（膝）、J143/J163（踝）、J071/J072（髋）
- 计算描述（CARD-FS:word/document.xml:P0150）：双踝垂直速度同向峰值 + 双踝相对地面基线的上移量 + 连续帧持续时间
- 不可评价/范围限制（CARD-FS:word/document.xml:P0162）：缺少稳定地面基线、脚部被遮挡或双踝有效帧低于70%时不可确认真实离地
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0152）：双脚轻微离地明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0154）：双脚轻微离地基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0156）：观察到双脚轻微离地，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0158）：双脚轻微离地很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0160）：未观察到双脚轻微离地
- 已有必需特征：bilateral_foot_rise_min_body [body]；bilateral_foot_rise_synchrony_ms [ms]；bilateral_foot_rise_proxy_duration_ms [ms]；hip_center_vertical_velocity_body_s [body/s]。需标定；不是原文阈值。
- 专项差异：FS-SOURCE-START。

### FS01-M04 双脚分开落地

- 原文定位：CARD-FS:word/document.xml:P0173；表格 T005。
- 定义：双脚接近同时落地并形成可向多个方向启动的支撑宽度。
- 原文状态：部分可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：双踝下降至局部最低点、左右踝水平距离、落地时差、髋中心稳定性
- 所需点：J143/J163（踝）、J141/J161（膝）、J071/J072（髋）
- 计算描述（CARD-FS:word/document.xml:P0185）：左右踝垂直速度由负转近零的时刻差 + 落地后踝距/髋宽比例 + 髋中心横向波动
- 不可评价/范围限制（CARD-FS:word/document.xml:P0197）：缺少脚跟/脚尖和地面标定时不能精确确认接触帧；双踝有效帧低于70%不可评价
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0187）：双脚分开落地明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0189）：双脚分开落地基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0191）：观察到双脚分开落地，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0193）：双脚分开落地很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0195）：未观察到双脚分开落地
- 已有必需特征：bilateral_foot_vertical_slowdown_time_offset_ms [ms]；post_slowdown_stance_width_body [body]；hip_center_lateral_variability_body [body]。需标定；不是原文阈值。
- 专项差异：FS-SOURCE-START、FS01-M04-SCALE。

### FS01-M05 重心重新分配并准备启动

- 原文定位：CARD-FS:word/document.xml:P0208；表格 T006。
- 定义：双脚落地后，身体中心回到支撑区域并形成可向来球方向启动的稳定状态。
- 原文状态：可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：髋中心相对双踝中点的位置、身体中心速度下降、双膝弹性、下一步启动前停留时间
- 所需点：J071/J072、J141/J161、J143/J163、J033/J034
- 计算描述（CARD-FS:word/document.xml:P0220）：髋中心投影是否位于双踝区间 + 落地后速度稳定度 + 到FS02启动的时间间隔
- 不可评价/范围限制（CARD-FS:word/document.xml:P0232）：双踝或双髋有效帧低于70%，或FS02启动事件未被识别
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0222）：重心重新分配明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0224）：重心重新分配基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0226）：观察到重心重新分配，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0228）：重心重新分配很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0230）：未观察到重心重新分配
- 已有必需特征：body_center_speed_body_s [body/s]；hip_center_relative_to_ankle_support [ratio]；torso_lean_deg [deg]；shoulder_hip_angular_velocity [deg/s]。需标定；不是原文阈值。
- 专项差异：FS-SOURCE-START、FS01-M05-CROSS-EVENT。

### FS02-M01 判断来球方向

- 原文定位：CARD-FS:word/document.xml:P0246；表格 T007。
- 定义：垫步落地后，根据来球方向形成明确的第一步启动方向。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：头部朝向、身体中心初始移动方向、球候选轨迹方向（如可用）
- 所需点：J004、J006/J007、J071/J072；可选 BALL-010/BALL-025
- 计算描述（CARD-FS:word/document.xml:P0258）：身体中心启动方向与球轨迹方向夹角；球轨迹不可用时仅输出“启动方向”，不判断方向是否正确
- 不可评价/范围限制（CARD-FS:word/document.xml:P0270）：活动球轨迹不可用时不得评价“与来球方向一致”；头部/髋部有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0260）：方向判断明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0262）：方向判断基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0264）：观察到方向判断，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0266）：方向判断很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0268）：未观察到方向判断
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS02-M02 重心向来球方向转换

- 原文定位：CARD-FS:word/document.xml:P0281；表格 T008。
- 定义：身体中心由中性准备位置向目标方向产生持续位移。
- 原文状态：可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：髋中心速度方向、肩髋整体位移、双踝支撑变化
- 所需点：J071/J072、J033/J034、J143/J163
- 计算描述（CARD-FS:word/document.xml:P0293）：髋中心二维速度矢量 + 肩髋中心方向一致性 + 启动前后位移连续性
- 不可评价/范围限制（CARD-FS:word/document.xml:P0305）：双髋有效帧低于70%或镜头发生明显移动且未补偿
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0295）：重心方向转换明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0297）：重心方向转换基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0299）：观察到重心方向转换，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0301）：重心方向转换很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0303）：未观察到重心方向转换
- 已有必需特征：body_center_speed_body_s [body/s]；hip_center_relative_to_ankle_support [ratio]；torso_lean_deg [deg]；launch_direction_deg [deg]；target_direction_alignment_error_deg [deg]。需标定；不是原文阈值。
- 专项差异：FS02-M02-TARGET。

### FS02-M03 支撑脚发力

- 原文定位：CARD-FS:word/document.xml:P0316；表格 T009。
- 定义：与启动方向相反一侧下肢伸展，推动身体向目标方向加速。
- 原文状态：部分可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：支撑侧膝角增大、踝髋距离变化、髋中心加速度、左右侧时序关系
- 所需点：J071/J072、J141/J161、J143/J163
- 计算描述（CARD-FS:word/document.xml:P0328）：支撑侧膝伸展速度 + 髋中心水平加速度 + 支撑侧动作早于启动脚离地的时序
- 不可评价/范围限制（CARD-FS:word/document.xml:P0340）：现有视觉不能测量真实蹬地力；支撑侧关键点有效帧低于70%不可评价
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0330）：支撑脚发力明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0332）：支撑脚发力基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0334）：观察到支撑脚发力，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0336）：支撑脚发力很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0338）：未观察到支撑脚发力
- 已有必需特征：launch_direction_deg [deg]；drive_side_code [code]；support_knee_extension_velocity_deg_s [deg/s]；hip_acceleration_along_launch_direction_body_s2 [body/s2]；support_drive_to_moving_foot_rise_proxy_ms [ms]。需标定；不是原文阈值。

### FS02-M04 启动脚离地

- 原文定位：CARD-FS:word/document.xml:P0351；表格 T010。
- 定义：启动脚离开原支撑位置并向目标方向迈出第一步。
- 原文状态：部分可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：启动侧踝点位移、踝点速度、左右踝相对距离变化、髋中心同步移动
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P0363）：启动侧踝速度超过阈值并持续朝目标方向移动 + 与支撑侧的相对位移变化
- 不可评价/范围限制（CARD-FS:word/document.xml:P0375）：缺少地面接触信息时只能识别“脚部启动”，不能精确确认离地；踝点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0365）：启动脚离地明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0367）：启动脚离地基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0369）：观察到启动脚离地，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0371）：启动脚离地很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0373）：未观察到启动脚离地
- 已有必需特征：launch_direction_deg [deg]；launch_side_code [code]；launch_foot_speed_peak_body_s [body/s]；launch_foot_relative_displacement_body [body]；launch_foot_motion_duration_ms [ms]。需标定；不是原文阈值。

### FS02-M05 第一步落地并建立移动方向

- 原文定位：CARD-FS:word/document.xml:P0386；表格 T011。
- 定义：启动脚完成第一步并建立后续连续移动的方向和新支撑。
- 原文状态：部分可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：启动踝速度下降、第一步位移长度、髋中心方向稳定、双脚新支撑关系
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P0398）：启动脚局部速度由高转低 + 第一落脚位移 + 落地后髋中心方向保持连续
- 不可评价/范围限制（CARD-FS:word/document.xml:P0410）：缺少脚部接触点时落地为估计事件；踝/髋有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0400）：第一步落地明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0402）：第一步落地基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0404）：观察到第一步落地，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0406）：第一步落地很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0408）：未观察到第一步落地
- 已有必需特征：launch_side_code [code]；launch_foot_speed_drop_body_s [body/s]；first_step_displacement_body [body]；post_step_hip_direction_consistency [ratio]；launch_foot_slowdown_to_post_hip_direction_ms [ms]；post_step_stance_width_body [body]。需标定；不是原文阈值。

### FS03-M01 保持来球方向判断

- 原文定位：CARD-FS:word/document.xml:P0424；表格 T012。
- 定义：交叉步移动过程中持续保持对来球方向的判断，并使移动方向与目标区域一致。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：头部稳定、身体中心移动方向、球轨迹方向（如可用）
- 所需点：J004、J006/J007、J071/J072；可选 BALL-010/BALL-025
- 计算描述（CARD-FS:word/document.xml:P0436）：头部角速度稳定度 + 身体中心方向变化率 + 与球轨迹方向的一致性
- 不可评价/范围限制（CARD-FS:word/document.xml:P0448）：活动球轨迹不可用时只评价移动方向稳定，不评价与来球是否一致
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0438）：方向保持明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0440）：方向保持基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0442）：观察到方向保持，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0444）：方向保持很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0446）：未观察到方向保持
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS03-M02 支撑脚持续发力

- 原文定位：CARD-FS:word/document.xml:P0459；表格 T013。
- 定义：连续交叉移动中，下肢交替推动身体保持中远距离横向速度。
- 原文状态：部分可评分。项目状态：event_implementation_required。
- 当前识别点：左右膝踝交替伸展、髋中心速度连续性、步间速度波动
- 所需点：J071/J072、J141/J161、J143/J163
- 计算描述（CARD-FS:word/document.xml:P0471）：左右下肢伸展峰值交替 + 髋中心速度曲线连续性 + 步间减速幅度
- 不可评价/范围限制（CARD-FS:word/document.xml:P0483）：不能由视觉直接测量真实发力；下肢关键点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0473）：持续推动明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0475）：持续推动基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0477）：观察到持续推动，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0479）：持续推动很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0481）：未观察到持续推动
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS03-M03 移动脚完成交叉跨步

- 原文定位：CARD-FS:word/document.xml:P0494；表格 T014。
- 定义：移动脚跨越另一侧脚的水平投影，形成交叉关系并扩大横向位移。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：左右踝水平顺序交换、踝轨迹交叉、步幅、身体平衡
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P0506）：左右踝在移动轴上的相对顺序发生交换 + 轨迹交叉 + 步幅/髋宽比例
- 不可评价/范围限制（CARD-FS:word/document.xml:P0518）：双踝有效帧低于70%，或机位接近纯侧面导致左右关系严重重叠
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0508）：交叉跨步明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0510）：交叉跨步基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0512）：观察到交叉跨步，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0514）：交叉跨步很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0516）：未观察到交叉跨步
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS03-M04 身体完成横向位移

- 原文定位：CARD-FS:word/document.xml:P0529；表格 T015。
- 定义：身体中心随交叉步完成连续的中远距离横向移动。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：髋中心横向位移、速度连续性、肩髋朝向稳定、上下波动
- 所需点：J071/J072、J033/J034、J141/J161、J143/J163
- 计算描述（CARD-FS:word/document.xml:P0541）：髋中心横向位移/身高 + 速度曲线平滑度 + 肩髋中心方向一致性
- 不可评价/范围限制（CARD-FS:word/document.xml:P0553）：髋部有效帧低于70%或相机移动未补偿
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0543）：横向位移明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0545）：横向位移基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0547）：观察到横向位移，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0549）：横向位移很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0551）：未观察到横向位移
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS03-M05 接近目标击球区域

- 原文定位：CARD-FS:word/document.xml:P0564；表格 T016。
- 定义：交叉步结束时身体速度开始下降，并进入可进行短距离调整或支撑的区域。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：身体中心减速、球与身体距离（如球可用）、步幅减小、后续FS04/FS05/FS06-08事件
- 所需点：J071/J072、J143/J163；可选 BALL-010；场地标定可选
- 计算描述（CARD-FS:word/document.xml:P0576）：髋中心速度下降 + 后续短步/支撑事件出现；有球轨迹时增加球—身体距离收敛
- 不可评价/范围限制（CARD-FS:word/document.xml:P0588）：球轨迹和后续支撑事件均不可用时，不能确认是否真正接近目标击球区域
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0578）：接近目标区域明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0580）：接近目标区域基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0582）：观察到接近目标区域，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0584）：接近目标区域很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0586）：未观察到接近目标区域
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS04-M01 保持来球方向判断

- 原文定位：CARD-FS:word/document.xml:P0602；表格 T017。
- 定义：并步移动过程中持续保持目标方向，不因跟随脚靠近而改变移动线路。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：头部稳定、髋中心方向、球轨迹方向（如可用）
- 所需点：J004、J006/J007、J071/J072；可选 BALL-010/BALL-025
- 计算描述（CARD-FS:word/document.xml:P0614）：头部稳定度 + 髋中心方向变化率 + 与球轨迹方向一致性
- 不可评价/范围限制（CARD-FS:word/document.xml:P0626）：活动球轨迹不可用时不能评价与来球方向一致性
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0616）：方向保持明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0618）：方向保持基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0620）：观察到方向保持，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0622）：方向保持很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0624）：未观察到方向保持
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS04-M02 支撑脚推动身体横向移动

- 原文定位：CARD-FS:word/document.xml:P0637；表格 T018。
- 定义：支撑侧下肢推动身体完成短距离横向位移。
- 原文状态：部分可评分。项目状态：event_implementation_required。
- 当前识别点：支撑侧膝伸展、髋中心横向加速度、踝点位置变化
- 所需点：J071/J072、J141/J161、J143/J163
- 计算描述（CARD-FS:word/document.xml:P0649）：支撑侧膝伸展速度 + 髋中心横向加速度 + 方向一致性
- 不可评价/范围限制（CARD-FS:word/document.xml:P0661）：视觉不能直接测量蹬地力；关键点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0651）：横向推动明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0653）：横向推动基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0655）：观察到横向推动，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0657）：横向推动很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0659）：未观察到横向推动
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS04-M03 移动脚向移动方向迈出

- 原文定位：CARD-FS:word/document.xml:P0672；表格 T019。
- 定义：移动脚向目标方向迈出短步，且双脚不发生交叉。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：移动侧踝位移、双踝相对顺序、步幅、移动方向
- 所需点：J143/J163、J141/J161
- 计算描述（CARD-FS:word/document.xml:P0684）：移动侧踝位移方向 + 双踝相对顺序保持不变 + 步幅/髋宽比例
- 不可评价/范围限制（CARD-FS:word/document.xml:P0696）：双踝有效帧低于70%或严重遮挡
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0686）：移动脚迈出明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0688）：移动脚迈出基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0690）：观察到移动脚迈出，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0692）：移动脚迈出很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0694）：未观察到移动脚迈出
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS04-M04 跟随脚并步跟进

- 原文定位：CARD-FS:word/document.xml:P0707；表格 T020。
- 定义：跟随脚向移动脚靠近，重新建立合适支撑宽度，完成一个并步周期。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：跟随侧踝向移动侧靠近、踝距先增后减、双脚顺序不交换、髋中心连续移动
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P0719）：踝距变化曲线 + 双踝顺序不交换 + 跟随脚位移延迟相对移动脚的时序
- 不可评价/范围限制（CARD-FS:word/document.xml:P0731）：双踝有效帧低于70%或脚部交叠无法区分
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0721）：并步跟进明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0723）：并步跟进基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0725）：观察到并步跟进，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0727）：并步跟进很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0729）：未观察到并步跟进
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS04-M05 接近目标击球区域

- 原文定位：CARD-FS:word/document.xml:P0742；表格 T021。
- 定义：并步完成后身体减速并进入小碎步或击球支撑的准备区域。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：髋中心减速、步幅减小、后续FS05/FS06-08事件、球—身体距离（如可用）
- 所需点：J071/J072、J143/J163；可选 BALL-010
- 计算描述（CARD-FS:word/document.xml:P0754）：髋中心速度下降 + 后续调整/支撑事件出现；球可用时计算球—身体距离
- 不可评价/范围限制（CARD-FS:word/document.xml:P0766）：球轨迹和后续支撑均不可用时只能判断减速，不能确认到位
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0756）：接近目标区域明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0758）：接近目标区域基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0760）：观察到接近目标区域，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0762）：接近目标区域很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0764）：未观察到接近目标区域
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS05-M01 判断球与身体距离

- 原文定位：CARD-FS:word/document.xml:P0780；表格 T022。
- 定义：接近击球区域后，根据球的位置、高度和预计击球点判断身体需要怎样微调。
- 原文状态：暂不可评分。项目状态：dependency_implementation_required。
- 当前识别点：球中心与髋中心/肩中心距离、球高度、身体移动方向（球稳定时）
- 所需点：J071/J072、J033/J034；必须 BALL-010/BALL-013
- 计算描述（CARD-FS:word/document.xml:P0792）：球—身体二维距离、球相对髋/肩高度、预测接触窗口；需稳定活动球轨迹
- 不可评价/范围限制（CARD-FS:word/document.xml:P0804）：活动球轨迹、击球事件或球员—球关联不可用
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0794）：球身距离判断明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0796）：球身距离判断基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0798）：观察到球身距离判断，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0800）：球身距离判断很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0802）：未观察到球身距离判断
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS05-M02 小步调整身体位置

- 原文定位：CARD-FS:word/document.xml:P0815；表格 T023。
- 定义：通过连续短步、高频低幅调整身体整体位置。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：双踝连续小幅位移、步频、单步长度、髋中心微调幅度
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P0827）：单位时间步数 + 单步长度/身高 + 髋中心总位移与净位移比
- 不可评价/范围限制（CARD-FS:word/document.xml:P0839）：双踝有效帧低于70%或视频帧率过低导致步序无法分辨
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0829）：小步调整明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0831）：小步调整基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0833）：观察到小步调整，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0835）：小步调整很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0837）：未观察到小步调整
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS05-M03 微调前后距离

- 原文定位：CARD-FS:word/document.xml:P0850；表格 T024。
- 定义：通过前后方向的小步修正身体与预计击球点的纵向距离。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：髋中心前后位移、双踝前后小步、球—身体纵向距离（如可用）
- 所需点：J071/J072、J143/J163；可选 BALL-010；场地单应性建议
- 计算描述（CARD-FS:word/document.xml:P0862）：球场纵向坐标中的小幅往复位移 + 步幅；无标定时只能做图像二维代理
- 不可评价/范围限制（CARD-FS:word/document.xml:P0874）：相机视角不支持前后方向分离，且无场地标定；球轨迹不可用时不能判断距离是否合理
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0864）：前后距离微调明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0866）：前后距离微调基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0868）：观察到前后距离微调，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0870）：前后距离微调很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0872）：未观察到前后距离微调
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS05-M04 微调左右距离

- 原文定位：CARD-FS:word/document.xml:P0885；表格 T025。
- 定义：通过左右方向的小步修正身体与预计击球点的横向距离。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：髋中心左右位移、双踝短步、球—身体横向距离（如可用）
- 所需点：J071/J072、J143/J163；可选 BALL-010
- 计算描述（CARD-FS:word/document.xml:P0897）：髋中心横向微位移 + 步幅 + 球—身体横向距离收敛
- 不可评价/范围限制（CARD-FS:word/document.xml:P0909）：活动球轨迹不可用时不能判断横向距离是否进入合理范围
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0899）：左右距离微调明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0901）：左右距离微调基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0903）：观察到左右距离微调，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0905）：左右距离微调很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0907）：未观察到左右距离微调
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS05-M05 建立击球支撑位置

- 原文定位：CARD-FS:word/document.xml:P0920；表格 T026。
- 定义：小碎步结束后双脚停止高频调整，形成稳定支撑并准备进入FS06/FS07/FS08。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：步频下降、双踝支撑关系稳定、髋中心速度下降、后续支撑类型事件
- 所需点：J143/J163、J141/J161、J071/J072、J033/J034
- 计算描述（CARD-FS:word/document.xml:P0932）：双踝速度下降 + 髋中心稳定度 + 支撑宽度保持 + 后续支撑分类
- 不可评价/范围限制（CARD-FS:word/document.xml:P0944）：双踝或双髋有效帧低于70%，或动作片段在支撑建立前结束
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0934）：击球支撑位置建立明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0936）：击球支撑位置建立基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0938）：观察到击球支撑位置建立，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0940）：击球支撑位置建立很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0942）：未观察到击球支撑位置建立
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS06-M01 判断开放式支撑位置

- 原文定位：CARD-FS:word/document.xml:P0958；表格 T027。
- 定义：根据身体与预计击球点关系，选择不明显跨步闭合的开放式支撑位置。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：球—身体关系、双脚横向关系、身体朝向、后续开放式支撑形成
- 所需点：J033/J034、J071/J072、J143/J163；可选 BALL-010
- 计算描述（CARD-FS:word/document.xml:P0970）：支撑类型分类结果 + 球—身体关系；无球时只判断是否形成开放式，不评价选择是否合理
- 不可评价/范围限制（CARD-FS:word/document.xml:P0982）：活动球轨迹不可用时不能评价开放式选择是否适合来球
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P0972）：开放式位置判断明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P0974）：开放式位置判断基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P0976）：观察到开放式位置判断，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P0978）：开放式位置判断很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P0980）：未观察到开放式位置判断
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS06-M02 外侧脚建立支撑

- 原文定位：CARD-FS:word/document.xml:P0993；表格 T028。
- 定义：靠近来球一侧的外侧脚落地并承担主要稳定作用。
- 原文状态：部分可评分。项目状态：event_implementation_required。
- 当前识别点：外侧踝速度下降、外侧膝屈曲、髋中心向外侧脚靠近、支撑持续时间
- 所需点：J141/J161、J143/J163、J071/J072
- 计算描述（CARD-FS:word/document.xml:P1005）：外侧脚估计落地时刻 + 外侧膝角 + 髋中心相对外侧踝位置
- 不可评价/范围限制（CARD-FS:word/document.xml:P1017）：缺少压力数据不能确认真实承重；外侧脚关键点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1007）：外侧脚支撑明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1009）：外侧脚支撑基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1011）：观察到外侧脚支撑，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1013）：外侧脚支撑很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1015）：未观察到外侧脚支撑
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS06-M03 双脚形成开放式站位

- 原文定位：CARD-FS:word/document.xml:P1028；表格 T029。
- 定义：双脚形成以横向展开为主的支撑关系，身体朝向保持相对打开。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：双踝连线与场地基准夹角、双肩/双髋朝向、前后脚错位程度
- 所需点：J143/J163、J071/J072、J033/J034；场地标定建议
- 计算描述（CARD-FS:word/document.xml:P1040）：踝线方向 + 前后坐标差/踝距 + 肩髋线方向；有场地标定时分类更可靠
- 不可评价/范围限制（CARD-FS:word/document.xml:P1052）：无场地方向基准且机位严重倾斜时只能做相对分类；踝点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1042）：开放式站位明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1044）：开放式站位基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1046）：观察到开放式站位，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1048）：开放式站位很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1050）：未观察到开放式站位
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS06-M04 重心进入支撑区域

- 原文定位：CARD-FS:word/document.xml:P1063；表格 T030。
- 定义：身体中心投影进入双脚形成的支撑区，并在击球前保持稳定。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：髋中心相对双踝区间、躯干倾斜、髋中心速度、双膝屈曲
- 所需点：J071/J072、J143/J163、J141/J161、J033/J034
- 计算描述（CARD-FS:word/document.xml:P1075）：髋中心投影相对双踝连线的位置 + 髋中心速度稳定度 + 躯干倾斜
- 不可评价/范围限制（CARD-FS:word/document.xml:P1087）：双髋或双踝有效帧低于70%；仅为视觉人体中心代理，不代表真实重心
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1077）：重心进入支撑区域明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1079）：重心进入支撑区域基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1081）：观察到重心进入支撑区域，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1083）：重心进入支撑区域很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1085）：未观察到重心进入支撑区域
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS06-M05 身体保持开放式击球准备

- 原文定位：CARD-FS:word/document.xml:P1098；表格 T031。
- 定义：开放式支撑完成后，身体保持稳定并可进入蹬地、转髋或挥拍阶段。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：支撑持续时间、肩髋朝向、髋中心稳定、后续GS动作启动
- 所需点：J033/J034、J071/J072、J141/J161、J143/J163
- 计算描述（CARD-FS:word/document.xml:P1110）：开放式支撑置信度 + 稳定保持时长 + 后续躯干/手臂动作的连续衔接
- 不可评价/范围限制（CARD-FS:word/document.xml:P1122）：支撑事件或后续击球动作未识别，或有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1112）：开放式击球准备明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1114）：开放式击球准备基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1116）：观察到开放式击球准备，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1118）：开放式击球准备很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1120）：未观察到开放式击球准备
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS07-M01 判断闭合式支撑位置

- 原文定位：CARD-FS:word/document.xml:P1136；表格 T032。
- 定义：根据来球和击球方向选择前脚跨出的闭合式支撑位置。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：球—身体关系、前后脚关系、身体侧身方向、后续闭合支撑形成
- 所需点：J033/J034、J071/J072、J143/J163；可选 BALL-010
- 计算描述（CARD-FS:word/document.xml:P1148）：支撑类型分类 + 前脚目标方向 + 球—身体关系；无球时只判断形成结果
- 不可评价/范围限制（CARD-FS:word/document.xml:P1160）：活动球轨迹不可用时不能评价位置选择是否合理
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1150）：闭合支撑位置判断明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1152）：闭合支撑位置判断基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1154）：观察到闭合支撑位置判断，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1156）：闭合支撑位置判断很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1158）：未观察到闭合支撑位置判断
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS07-M02 前脚向击球方向跨出

- 原文定位：CARD-FS:word/document.xml:P1171；表格 T033。
- 定义：前脚向来球或击球方向跨出并形成稳定落点。
- 原文状态：部分可评分。项目状态：event_implementation_required。
- 当前识别点：前脚踝位移、步幅、方向、落地后速度下降、膝屈曲
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P1183）：前脚踝位移/身高 + 位移方向 + 估计落地时刻 + 膝角
- 不可评价/范围限制（CARD-FS:word/document.xml:P1195）：缺少脚尖和地面接触信息时不能精确判断脚掌方向与真实落地
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1185）：前脚跨出明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1187）：前脚跨出基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1189）：观察到前脚跨出，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1191）：前脚跨出很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1193）：未观察到前脚跨出
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS07-M03 双脚形成闭合式站位

- 原文定位：CARD-FS:word/document.xml:P1206；表格 T034。
- 定义：前后脚关系明确，身体侧身姿态增加，形成闭合式击球支撑。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：双踝前后错位、踝线与场地基准夹角、肩髋侧身角度
- 所需点：J143/J163、J071/J072、J033/J034；场地标定建议
- 计算描述（CARD-FS:word/document.xml:P1218）：前后脚坐标差/踝距 + 肩线/髋线相对场地方向夹角
- 不可评价/范围限制（CARD-FS:word/document.xml:P1230）：无场地基准且机位不支持前后方向分离；踝或肩髋有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1220）：闭合式站位明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1222）：闭合式站位基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1224）：观察到闭合式站位，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1226）：闭合式站位很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1228）：未观察到闭合式站位
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS07-M04 重心进入前后脚支撑区域

- 原文定位：CARD-FS:word/document.xml:P1241；表格 T035。
- 定义：身体中心进入前后脚形成的支撑区域，并保持平衡。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：髋中心相对前后脚连线、躯干倾斜、双膝状态、髋中心速度
- 所需点：J071/J072、J143/J163、J141/J161、J033/J034
- 计算描述（CARD-FS:word/document.xml:P1253）：髋中心投影至前后脚连线的比例 + 髋中心稳定度 + 躯干倾斜
- 不可评价/范围限制（CARD-FS:word/document.xml:P1265）：双髋或双踝有效帧低于70%；结果为视觉代理重心
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1255）：前后脚支撑区域明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1257）：前后脚支撑区域基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1259）：观察到前后脚支撑区域，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1261）：前后脚支撑区域很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1263）：未观察到前后脚支撑区域
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS07-M05 身体保持闭合式击球准备

- 原文定位：CARD-FS:word/document.xml:P1276；表格 T036。
- 定义：闭合式支撑完成后保持侧身、稳定和可衔接击球的状态。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：支撑保持时长、肩髋侧身、髋中心稳定、后续GS动作衔接
- 所需点：J033/J034、J071/J072、J141/J161、J143/J163
- 计算描述（CARD-FS:word/document.xml:P1288）：闭合式分类置信度 + 稳定保持时长 + 后续击球动作连续性
- 不可评价/范围限制（CARD-FS:word/document.xml:P1300）：支撑事件或后续击球动作未识别，或有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1290）：闭合式击球准备明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1292）：闭合式击球准备基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1294）：观察到闭合式击球准备，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1296）：闭合式击球准备很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1298）：未观察到闭合式击球准备
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS08-M01 判断跨步支撑位置

- 原文定位：CARD-FS:word/document.xml:P1314；表格 T037。
- 定义：识别常规支撑距离不足，需要通过大幅伸展步接近击球点。
- 原文状态：暂不可评分。项目状态：dependency_implementation_required。
- 当前识别点：球—身体距离、来球高度、常规支撑后的剩余距离、后续大跨步
- 所需点：J071/J072、J143/J163；必须 BALL-010/BALL-013
- 计算描述（CARD-FS:word/document.xml:P1326）：预测击球点与身体可达范围差值 + 来球高度 + 大跨步触发条件
- 不可评价/范围限制（CARD-FS:word/document.xml:P1338）：活动球轨迹、击球点预测或球员—球关联不可用
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1328）：跨步位置判断明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1330）：跨步位置判断基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1332）：观察到跨步位置判断，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1334）：跨步位置判断很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1336）：未观察到跨步位置判断
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS08-M02 跨步脚向来球方向伸出

- 原文定位：CARD-FS:word/document.xml:P1349；表格 T038。
- 定义：一侧脚以明显大于普通调整步的幅度向目标方向跨出。
- 原文状态：部分可评分。项目状态：event_implementation_required。
- 当前识别点：跨步侧踝位移、步幅/身高、方向、膝屈曲、估计落地
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P1361）：跨步侧踝单步位移/身高 + 位移方向 + 膝角变化
- 不可评价/范围限制（CARD-FS:word/document.xml:P1373）：缺少脚尖和地面接触信息时不能精确判断脚部朝向与落地质量
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1363）：跨步脚伸出明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1365）：跨步脚伸出基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1367）：观察到跨步脚伸出，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1369）：跨步脚伸出很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1371）：未观察到跨步脚伸出
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS08-M03 支撑脚保持稳定

- 原文定位：CARD-FS:word/document.xml:P1384；表格 T039。
- 定义：原支撑脚在跨步过程中保持相对稳定，与跨步脚共同形成伸展支撑。
- 原文状态：部分可评分。项目状态：event_implementation_required。
- 当前识别点：支撑侧踝位移较小、膝髋稳定、髋中心不过度越出支撑范围
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P1396）：支撑侧踝位移/跨步侧踝位移比 + 支撑侧膝角稳定度 + 髋中心偏移
- 不可评价/范围限制（CARD-FS:word/document.xml:P1408）：不能确认真实承重；支撑侧关键点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1398）：支撑脚稳定明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1400）：支撑脚稳定基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1402）：观察到支撑脚稳定，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1404）：支撑脚稳定很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1406）：未观察到支撑脚稳定
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS08-M04 身体形成伸展支撑

- 原文定位：CARD-FS:word/document.xml:P1419；表格 T040。
- 定义：跨步脚与支撑脚形成大范围支撑，上半身接近目标击球区域且保持可控。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：双踝距离增大、肩髋中心向目标方向伸展、躯干倾斜、身体稳定
- 所需点：J033/J034、J071/J072、J143/J163、J141/J161
- 计算描述（CARD-FS:word/document.xml:P1431）：踝距/髋宽比例 + 肩髋中心与支撑区关系 + 躯干倾斜和速度稳定度
- 不可评价/范围限制（CARD-FS:word/document.xml:P1443）：肩髋或双踝有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1433）：伸展支撑明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1435）：伸展支撑基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1437）：观察到伸展支撑，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1439）：伸展支撑很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1441）：未观察到伸展支撑
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS08-M05 重心进入跨步支撑区域

- 原文定位：CARD-FS:word/document.xml:P1454；表格 T041。
- 定义：身体中心进入跨步脚与原支撑脚形成的支撑区，并维持击球所需平衡。
- 原文状态：可评分。项目状态：event_implementation_required。
- 当前识别点：髋中心相对双踝区间、髋中心速度、躯干倾斜、双膝状态
- 所需点：J071/J072、J143/J163、J141/J161、J033/J034
- 计算描述（CARD-FS:word/document.xml:P1466）：髋中心投影在双踝区间的位置 + 髋中心稳定度 + 躯干倾斜
- 不可评价/范围限制（CARD-FS:word/document.xml:P1478）：双髋或双踝有效帧低于70%；为视觉代理重心
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1468）：跨步支撑区域明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1470）：跨步支撑区域基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1472）：观察到跨步支撑区域，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1474）：跨步支撑区域很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1476）：未观察到跨步支撑区域
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS09-M01 判断身体惯性方向

- 原文定位：CARD-FS:word/document.xml:P1492；表格 T042。
- 定义：根据身体中心移动方向、速度和重心偏移，识别需要制动的方向。
- 原文状态：可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：髋中心速度矢量、肩髋朝向、人体中心相对双脚偏移
- 所需点：J071/J072、J033/J034、J143/J163
- 计算描述（CARD-FS:word/document.xml:P1504）：髋中心速度方向与大小 + 髋中心相对双踝中点偏移 + 速度变化趋势
- 不可评价/范围限制（CARD-FS:word/document.xml:P1516）：双髋或双踝有效帧低于70%，或相机移动未补偿
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1506）：惯性方向判断明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1508）：惯性方向判断基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1510）：观察到惯性方向判断，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1512）：惯性方向判断很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1514）：未观察到惯性方向判断
- 已有必需特征：hip_center_speed_body_s [body/s]；hip_center_motion_direction_deg [deg]；hip_center_relative_to_ankle_midpoint_x_body [body]；hip_center_relative_to_ankle_midpoint_y_body [body]；hip_center_speed_trend_body_s2 [body/s2]。需标定；不是原文阈值。

### FS09-M02 制动脚落地

- 原文定位：CARD-FS:word/document.xml:P1527；表格 T043。
- 定义：根据惯性方向，制动脚形成新的减速支撑点。
- 原文状态：部分可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：制动侧踝速度下降、踝点落点、膝屈曲、身体中心减速时序
- 所需点：J143/J163、J141/J161、J071/J072
- 计算描述（CARD-FS:word/document.xml:P1539）：制动侧踝局部速度由高转低 + 膝角减小 + 髋中心减速度峰值时序
- 不可评价/范围限制（CARD-FS:word/document.xml:P1551）：缺少地面接触信息时落地为估计；关键点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1541）：制动脚落地明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1543）：制动脚落地基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1545）：观察到制动脚落地，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1547）：制动脚落地很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1549）：未观察到制动脚落地
- 已有必需特征：left_ankle_speed_body_s [body/s]；right_ankle_speed_body_s [body/s]；left_ankle_speed_drop_body_s [body/s]；right_ankle_speed_drop_body_s [body/s]；braking_side_code [code]；braking_ankle_speed_drop_body_s [body/s]；left_knee_flexion_change_deg [deg]；right_knee_flexion_change_deg [deg]；hip_center_deceleration_body_s2 [body/s2]；braking_ankle_slowdown_to_hip_deceleration_ms [ms]。需标定；不是原文阈值。

### FS09-M03 下肢吸收身体惯性

- 原文定位：CARD-FS:word/document.xml:P1562；表格 T044。
- 定义：制动后双膝屈曲、髋中心下降，身体速度明显降低。
- 原文状态：可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：双膝角减小、髋中心下降、身体中心减速度、躯干稳定
- 所需点：J071/J072、J141/J161、J143/J163、J033/J034
- 计算描述（CARD-FS:word/document.xml:P1574）：髋中心速度下降率 + 双膝屈曲变化 + 髋中心下移量 + 躯干角波动
- 不可评价/范围限制（CARD-FS:word/document.xml:P1586）：双膝或双髋有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1576）：下肢吸收惯性明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1578）：下肢吸收惯性基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1580）：观察到下肢吸收惯性，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1582）：下肢吸收惯性很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1584）：未观察到下肢吸收惯性
- 已有必需特征：hip_center_deceleration_body_s2 [body/s2]；hip_center_speed_drop_body_s [body/s]；left_knee_flexion_change_deg [deg]；right_knee_flexion_change_deg [deg]；hip_height_delta_body [body]；torso_lean_variability_deg [deg]。需标定；不是原文阈值。

### FS09-M04 重心重新稳定

- 原文定位：CARD-FS:word/document.xml:P1597；表格 T045。
- 定义：制动后身体中心重新进入双脚支撑区域，速度和摆动趋于稳定。
- 原文状态：可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：髋中心相对双踝中点、髋中心速度、肩髋摆动、支撑宽度
- 所需点：J071/J072、J143/J163、J033/J034
- 计算描述（CARD-FS:word/document.xml:P1609）：髋中心投影回到双踝区间 + 速度低于阈值持续若干帧 + 肩髋角速度下降
- 不可评价/范围限制（CARD-FS:word/document.xml:P1621）：双髋或双踝有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1611）：重心重新稳定明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1613）：重心重新稳定基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1615）：观察到重心重新稳定，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1617）：重心重新稳定很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1619）：未观察到重心重新稳定
- 已有必需特征：hip_center_relative_to_ankle_support [ratio]；hip_center_relative_to_ankle_midpoint_x_body [body]；hip_center_relative_to_ankle_midpoint_y_body [body]；hip_center_speed_drop_body_s [body/s]；stance_width_body [body]；stability_duration_ms [ms]；shoulder_hip_angular_velocity_change_deg_s [deg/s]；double_support_proxy_duration_ms [ms]。需标定；不是原文阈值。

### FS09-M05 身体进入稳定控制状态

- 原文定位：CARD-FS:word/document.xml:P1632；表格 T046。
- 定义：身体速度降低、双脚重新形成支撑，并可进入回位、再次启动或下一动作。
- 原文状态：可评分。项目状态：implemented_f2_calibration_required。
- 当前识别点：髋中心速度低位保持、双踝支撑稳定、肩髋角速度下降、后续事件可衔接
- 所需点：J033/J034、J071/J072、J143/J163、J141/J161
- 计算描述（CARD-FS:word/document.xml:P1644）：人体中心速度稳定度 + 双脚支撑持续时间 + 后续FS10/FS02事件连续性
- 不可评价/范围限制（CARD-FS:word/document.xml:P1656）：片段在稳定完成前结束，或关键点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1646）：稳定控制状态明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1648）：稳定控制状态基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1650）：观察到稳定控制状态，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1652）：稳定控制状态很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1654）：未观察到稳定控制状态
- 已有必需特征：stability_duration_ms [ms]；hip_center_speed_drop_body_s [body/s]；double_support_proxy_duration_ms [ms]；torso_lean_variability_deg [deg]；shoulder_hip_angular_velocity_change_deg_s [deg/s]；hip_deceleration_to_double_support_proxy_ms [ms]。需标定；不是原文阈值。
- 专项差异：FS09-M05-CROSS-EVENT。

### FS10-M01 判断回位目标位置

- 原文定位：CARD-FS:word/document.xml:P1670；表格 T047。
- 定义：根据自身位置、球路、对手位置和战术目标确定合理回位区域，而非机械回到中心。
- 原文状态：暂不可评分。项目状态：dependency_implementation_required。
- 当前识别点：球员场地坐标、球轨迹、对手位置、击球后位置、目标区域
- 所需点：目标球员 track_id、对手 track_id、BALL-010/BALL-025/BALL-026、场地单应性
- 计算描述（CARD-FS:word/document.xml:P1682）：基于场地坐标、球轨迹和对手位置预测合理回位区域，再比较实际回位方向
- 不可评价/范围限制（CARD-FS:word/document.xml:P1694）：缺少可信场地标定、活动球轨迹或对手身份关联
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1684）：回位目标判断明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1686）：回位目标判断基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1688）：观察到回位目标判断，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1690）：回位目标判断很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1692）：未观察到回位目标判断
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS10-M02 启动回位移动

- 原文定位：CARD-FS:word/document.xml:P1705；表格 T048。
- 定义：制动稳定后，身体中心开始朝回位目标方向移动。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：髋中心从低速状态重新加速、双踝启动、回位方向、启动延迟
- 所需点：J071/J072、J143/J163、J141/J161；场地标定建议
- 计算描述（CARD-FS:word/document.xml:P1717）：FS09结束至髋中心重新加速的时间 + 移动方向 + 双脚启动事件
- 不可评价/范围限制（CARD-FS:word/document.xml:P1729）：无法确定回位目标时只能评价“是否启动回位”，不能评价方向是否正确
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1719）：启动回位明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1721）：启动回位基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1723）：观察到启动回位，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1725）：启动回位很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1727）：未观察到启动回位
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。
- 专项差异：FS-OPTIONAL-DEPS。

### FS10-M03 调整回位路线

- 原文定位：CARD-FS:word/document.xml:P1740；表格 T049。
- 定义：回位过程中根据目标区域持续调整方向和距离，保持移动连续。
- 原文状态：条件可评分。项目状态：dependency_implementation_required。
- 当前识别点：髋中心轨迹曲率、速度连续性、场地坐标变化、球路变化（如可用）
- 所需点：J071/J072、J143/J163；可选 BALL-025/BALL-026；场地标定
- 计算描述（CARD-FS:word/document.xml:P1752）：场地坐标轨迹平滑度 + 路线偏差 + 速度连续性；无标定时仅做图像轨迹
- 不可评价/范围限制（CARD-FS:word/document.xml:P1764）：无场地标定且相机透视明显；无法确定目标回位区域
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1754）：调整回位路线明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1756）：调整回位路线基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1758）：观察到调整回位路线，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1760）：调整回位路线很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1762）：未观察到调整回位路线
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS10-M04 到达合理场地位置

- 原文定位：CARD-FS:word/document.xml:P1775；表格 T050。
- 定义：回位结束时，球员进入可覆盖下一拍的合理场地区域。
- 原文状态：暂不可评分。项目状态：dependency_implementation_required。
- 当前识别点：球员场地坐标、与底线/边线/球网距离、速度下降、目标区域重合度
- 所需点：目标球员 track_id、场地单应性；可选对手与球轨迹
- 计算描述（CARD-FS:word/document.xml:P1787）：实际回位终点与动态目标区域的距离 + 与关键场地线距离 + 终点速度
- 不可评价/范围限制（CARD-FS:word/document.xml:P1799）：缺少可信场地标定或动态回位目标
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1789）：到达合理位置明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1791）：到达合理位置基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1793）：观察到到达合理位置，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1795）：到达合理位置很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1797）：未观察到到达合理位置
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。

### FS10-M05 建立下一拍空间准备位置

- 原文定位：CARD-FS:word/document.xml:P1810；表格 T051。
- 定义：回位后身体速度下降、重心稳定，并可再次进入分腿垫步或第一步启动。
- 原文状态：可评分。项目状态：dependency_implementation_required。
- 当前识别点：髋中心稳定、双脚支撑、身体朝向、后续FS01/FS02事件
- 所需点：J033/J034、J071/J072、J141/J161、J143/J163；场地信息可选
- 计算描述（CARD-FS:word/document.xml:P1822）：髋中心速度低位保持 + 双脚支撑稳定 + 身体朝向稳定 + 后续FS01/FS02衔接
- 不可评价/范围限制（CARD-FS:word/document.xml:P1834）：片段在准备状态形成前结束，或关键点有效帧低于70%
- 单位：源文没有统一标准单位；比例分母见计算描述。数值优化方向、数值等级界线、权重、扣分、聚合公式均未给出。
- A级（CARD-FS:word/document.xml:P1824）：下一拍空间准备明显、连续、时序合理，且无明显失衡
- B级（CARD-FS:word/document.xml:P1826）：下一拍空间准备基本完成，幅度、速度或节奏略有不足
- C级（CARD-FS:word/document.xml:P1828）：观察到下一拍空间准备，但连续性、协调性或稳定性一般
- D级（CARD-FS:word/document.xml:P1830）：下一拍空间准备很小、方向不清或出现明显停顿/失衡
- E级（CARD-FS:word/document.xml:P1832）：未观察到下一拍空间准备
- 尚无该指标的13项执行评分需求契约；不得因卡片在目录中出现而宣称已实现。
- 专项差异：FS-OPTIONAL-DEPS。

## 示例与来源优先级

CARD-FS没有独立的数值评分示例，更没有通用百分制示例。A～E文字是正式卡片的等级描述，反馈是建议文案，不构成可计算阈值。VIS-FS有类比与例子（如并步“螃蟹一样”、回位拉斜线站中线一侧），不应转成硬编码角度或落点。

源文标题与文件名版本不一致；两份文档没有共同指定全局优先级。建议正式指标以CARD-FS逐项依据，技术识别语义以VIS-FS补充，完整评分同时遵守卡片和附录的证据限制。此为明确建议，不冒充源文规定；未决差异必须由规则维护者确认。
