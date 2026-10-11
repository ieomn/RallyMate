# RallyMate 项目完整代码逻辑说明

这套项目把网球视频转换为可回放的二维观测、动作候选、逐项测量和证据参考报告，同时提供人工真值、误差评估、模型训练与校准准入工具。理解它最关键的区分是：**看见某个关键点、检测到一段运动、测出一个数值、允许使用该数值评分、获得经教练校准的等级，是不同步骤。** 项目用数据合同、质量门控、来源指纹和可信注册表约束这些步骤。

阅读基线：2026-10-09，本地分支 `codex/frontend-remove-manual-scoring`，提交 `70a744d54be0d958ed241e6ead3259a759888882`。分析开始时工作树无已有修改。本次只新增代码说明与索引，没有修改业务代码、模型、运行任务或部署配置。

## 阅读入口

本说明采用总览加分篇，避免把几十万行项目压缩成一张目录表。

- [视觉推理、跟踪、事件与特征](code-logic-parts/vision.md)：模型如何逐帧工作、怎么选主球员、怎么形成动作候选与测量。
- [评分、校准、标注、评估与训练](code-logic-parts/scoring-research.md)：正式分为何仍受限、研究代码怎样提高证据可信度。
- [服务端、任务队列与报告装配](code-logic-parts/service.md)：分块上传、SQLite、worker、全部 HTTP 路由、参考分公式、兼容与部署。
- [前端页面、交互、代理与结果展示](code-logic-parts/frontend.md)：页面入口、状态流、恢复、回放、建议与导出。
- [全量源码定位索引](code-logic-parts/source-index.md)：逐文件的主要函数、类、方法、位置及 Python 项目内依赖；另有 [机器可读 JSON](code-logic-parts/source-index.json)。

## 1. 项目规模与说明边界

该提交共有 957 个 Git 跟踪文件。索引收录其中 623 个代码文件、203,984 行（包含注释和空行），覆盖 Python、TS/TSX、JS/MJS、PowerShell、shell、CSS、HTML、SQL；其中 Python 文件 476 个。没有把依赖包、模型权重、私人视频、构建缓存、未跟踪文件或 docs/reports 中的生成页面计入代码量。

`src` 有 11 个 Python 包目录，184 个代码/静态资源文件、100,554 行；网页目录有 97 个代码文件、12,044 行；scripts 有 166 个脚本、38,383 行；tests 有 167 个文件、51,320 行。网页还另有 24 个测试文件，已包含在网页统计内。contracts 包含 128 个 JSON Schema。

这里的“完整”指全项目模块地图、在线核心调用链、关键算法和状态分支、离线研究链，以及逐文件定位覆盖；不等于对 20 万行逐行翻译，也不等于已经对所有实验组合进行运行验收。主链路以实际源码为依据，研究辅助模块通过入口、关键实现、数据合同与调用关系归类；不把旧文档中的计划当作当前已运行功能。

## 2. 先把系统分成四条链

### 2.1 用户在线分析链

```text
浏览器选择视频
→ 同源 /v1/* 网关（开发时 Vite 代理，生产时 Worker gateway）
→ FastAPI 分块上传/传统上传
→ request.json + SQLite queued
→ 常驻 GPU worker
→ YOLO Detect + RTMPose Pose
→ frames.jsonl
→ 主球员时间线
→ FS 步伐事件候选 + 挥拍/阶段候选
→ 特征与质量门控
→ summary / 指标 / scores / 回放
→ API 报告装配
→ 网页参考分、逐项证据、动作回放、球路、导出
```

这是大多数用户操作实际经过的链。网页不是模型运行容器，HTTP API 也不是逐帧神经网络实现所在的位置。

### 2.2 原始产物的二次读取链

```text
已经存在的 frames.jsonl / primary-player.jsonl / events.jsonl
→ 轨迹重建、骨架窗口、动作候选版本更新、报告投影
→ 网页查看
```

球路和骨架接口、部分历史报告升级不用重跑 YOLO/RTMPose。它们是对同一份原始观测的派生视图。模型重跑与报告重建因此具有不同成本和可追溯边界。

### 2.3 研究与证据修复链

```text
原始视频与基线产物
→ 模型/裁剪/分辨率/关键点融合等候选实验
→ 固定帧/固定事件/固定场景比较
→ 人工事件或关键点真值
→ 缺口与误差分解
→ 是否改善可测性与准确性的报告
```

这解释了为何 scripts/evaluation 中有大量 M66–M97 命名：它们是阶段实验、审计与证据包，不是每次上传都执行的数十个模型阶段。实验产生更多关键点，不自动使线上评分合法。

### 2.4 校准与模型发布链

```text
教练标注/真值
→ 严格 intake 与来源绑定
→ 数据集分组与无泄漏切分
→ 阈值/有序回归候选拟合
→ 独立测试
→ 晋升决策与可信账本
→ 运行模型/视角/视频证据绑定
→ 满足运行时门控后才允许正式等级
```

模型训练和评分校准也不同：训练调整 detector/pose 权重；校准建立测量特征到教练等级的映射。更大的 pose 模型不能代替教练标注与校准。另有 ranking-only 相对排序研究支线，即使独立排序测试通过，也不能据此输出 A–E。

## 3. 按目录理解责任

- `src/rallymate_vision`：视频输入、模型接口、逐帧主循环、检测跟踪、时间基准、镜头运动、回放与产物验证。
- `src/rallymate_tracking`：整段视频主球员选择与时间线。
- `src/rallymate_events`：姿态运动事件候选、关联、候选差异与事件评估。
- `src/rallymate_features`：坐标、几何、平滑、速度/加速度、事件窗口测量、来源对齐特征与有效性。
- `src/rallymate_scoring`：可行性注册表、指标特征资格、正式评分、质量策略、校准、技术证据评估、挥拍/阶段/转体分析。
- `src/rallymate_service`：上传、异步任务、认证与 HTTP 边界、结果装配、证据参考分、评审持久化。
- `src/rallymate_annotation`：真值采集、工作清单、授权、标注交接、独立评审与 intake。
- `src/rallymate_evaluation`：诊断、误差预算、固定边界对比、多视频实验、人工真值一致性评估。
- `src/rallymate_pose_evaluation`：pose 基线的专门评估。
- `src/rallymate_training`：通用 YOLO 训练、模型登记，MMPose 数据集导出与严格微调流程。
- `src/rallymate_visualization`：离线观测/诊断页面生成；与产品网页属于不同输出入口。
- `scoring-demo-web`：当前产品网页、客户端数据合同、Cloudflare/Vite 网关、建议接口与导出。
- `contracts`：JSON 数据结构合同，定义 request、frame、event、feature、score、校准、真值和报告等可接受字段。
- `models/rtmpose`：预设与候选配置清单；实际模型二进制不在 Git 内。
- `calibration`：可信资源模板；模板不等于已经授权的生产校准。
- `training`：训练配置与执行模板。
- `scripts`：CLI 包装、数据编译、回归、报告、实验运行、启动和运维；逐项入口在源码索引。
- `deploy`：Docker、Nginx、AutoDL、进程监管与监控。
- `data/examples/reports/docs`：分析计划、请求或标签样例、选定历史证据、设计与使用说明。
- `runs/service_data/model_registry/runtime/FULL-TEST`：本机任务、模型环境、私人视频等运行资产，很多内容受 .gitignore 排除；不能把本机目录存在视为克隆后自动具备。

根目录几个大 JSON 具有不同权限：`registry-lifecycle.json` 指定当前权威；`metric-feasibility-pose-wave-v2.json` 是当前运行指标；`metric-feasibility.json` 是历史版本；`metric-measurement-plans.json` 是规划，不是当前启用清单。

## 4. 一次上传的完整时序

1. 访问 `/` 且没有 view/job 参数时，`app/page.tsx` 转到 `/welcome`。进入分析工作区才挂载 ScoreLab。
2. ScoreLab 组织文件、视频预览、上传/任务状态、结果和导航；ProductWorkspace 负责用户界面布局。
3. resumable-upload 按会话协议建立 upload ID、切字节块、计算 SHA-256、最多两个并发请求、重试和恢复已有块。
4. 网关只接受规定路径/方法，将请求发到 Python API，并注入服务端内部 Bearer key。
5. UploadStore 保存分块与 manifest；complete 校验完整性并调用统一的 `_submit_video`。
6. `_submit_video` 验证视频与注册表，固定技术规则版本，保存原视频和 request.json，再插入 queued 行，立刻返回 202。
7. worker 原子领取任务并续租，使用常驻 detector 与 pose 实例进入 `run_pipeline`。
8. pipeline 解码、选择时间窗、建立真实时间与镜头运动信息，逐帧识别人/球/拍与姿态，写 frames.jsonl；持续上报进度。
9. 后处理建立主球员时间线、事件候选与特征，做质量/资格/校准检查，生成指标和评分状态，再建立摘要、报告和回放。
10. worker 成功后把 summary 写 SQLite，任务变 succeeded。失败会保留明确错误和 failed 状态。
11. 浏览器轮询到成功，读取 demo-result、技术证据和回放地址；轨迹在 running 阶段也可以从已写帧构建。
12. 选择片段、播放视频、显示骨架与球路，只读取已有产物；导出是对已验证报告的再次表示。

主入口定位：`ScoreLab.tsx:L100` → `api.py:L780` → `database.py:L132` → `worker.py:L197` → `pipeline.py:L397` → `loop.py:L184` → `api.py:L1098`。

## 5. 数据对象怎样一层层变化

### 5.1 请求层

request.json 描述“分析哪个视频、输出到哪里、采用哪个模型、处理哪个时间窗、使用哪个指标注册表和校准资源”。它是本次任务的配置快照。source.video_path、output.directory 是服务端路径，网页不应自行提交任意 worker 路径。

### 5.2 帧观测层

frames.jsonl 每行对应一次已处理帧观测。它承载源时间/帧索引、人物/球/球拍检测框、track、人体关键点、后端拓扑信息、镜头运动与质量信息。这里的置信度是模型或规则的观测置信度，不是准确率报告。

人体 native Halpe26/wholebody 和 canonical COCO17 需要映射和有效性信息。一个 slot 存在并不等于该解剖关键点由模型直接观测到；估计、映射和缺失都要区分。

### 5.3 人物与事件层

primary-player.jsonl 回答“这一帧哪个 track 是目标运动员”。events.jsonl 回答“这个人的哪段时间属于哪个运动候选”。事件身份将 video、person、event_code、起止时间绑定。

主球员切换、时间缺口、镜头参考变化会影响连续窗口。当前事件/特征逻辑会在相机参考失效或参考时期变化处切分或拒绝窗口；原文对齐补充测量还严格要求源帧相邻。不过，现役通用 `kinematics.derivative` 会寻找前后有限邻点，仍可能跨过缺失帧计算导数；严格连续邻点导数函数目前是候选实现，不能把所有速度值都理解为经过同样的连续性保证。具体门控与限制见视觉分篇第 8、9、13 节。

### 5.4 特征与指标层

features.jsonl 保存事件级几何/运动学特征；indicator-features.jsonl 把它们组合成各指标所需的特征向量、measurement/scoring 状态、质量原因和上下文。

必须区分：`feature.value` 有数值、`feature.valid` 为真、整个指标已测、允许评分，是逐级更严格的条件。部分单项测量可用时，整组仍可能不可评分。

### 5.5 评分与报告层

scores.jsonl 是正式评分逻辑的逐指标输出，校准缺失等情形返回状态和空等级。summary 汇总过程、质量、产物、动作、注册表和运行 provenance。demo-result 再加入面向用户的参考分、回看片段与建议；它不会因为上游有某个数字就直接称之为技术分。

## 6. 五个必须分开的数值概念

1. **测量值**：例如膝角变化、归一化位移、速度、时间差。描述二维观测量。
2. **测量证据参考分**：服务层按可测比例、特征覆盖、置信度、重复性、评分证据比例组合的 0–100 分。主公式基础权重为 30/20/15/25/10；缺失分量权重会重新归一化。
3. **信息成型/分析完成度**：user_demo 中保留的兼容指标，衡量信息覆盖，与主参考分不是同一公式。
4. **技术证据就绪度**：24 项目录中，根据必需与增强证据覆盖计算适用性/可观察程度，不是技术好坏。
5. **正式 A–E**：需要具备准入条件的注册表、独立教练校准、可信晋升和运行绑定。当前运行注册表 13 项均为 F2，当前用户报告 formal_scoring 固定不可用。

因此页面显示高参考分仍可能没有正式等级；一个指标未测不代表该动作是 E；完整分析可能合法地产出零个可靠候选。详细公式、分母、去重、缺失处理和聚合方式见服务端分篇第 9 节。

## 7. 实际能力与容易误读的代码

### 7.1 框架名字

网页使用 React 19 与 vinext/Vite，兼容 Next 风格 app router 和 next/navigation。实际 npm 脚本是 vinext dev/build/start。代理在 Vite 插件和 Cloudflare worker gateway 中，不是 next.config rewrites。

### 7.2 当前页面与保留文件

当前默认页是宣传页，工作台通过 view/job 参数进入。当前提交移除的是人工评级提交表单；`SourceMeasurementPanel` 的只读原文对应测量，以及 `RuleReviewPanel` 的只读原文核验，仍由 `LiveResults.tsx:L120` 渲染，后者位于折叠的“证据参考分与完整原文核验”区域。technical-review API/客户端及台账模型仍保留，但当前页面没有调用人工评审保存表单。旧 scoring/engine/scenarios 也是历史演示逻辑，不能当真实视频评分主路径。

### 7.3 球场

court.py 和请求 auto/manual 选项仍存在，但当前 run_pipeline 实际逐帧路径强制使用 disabled 球场观测。这不能解释成在线已完成真实球场单应性标定或真实米制球速。

### 7.4 视频回放

产物名仍叫 annotated.mp4，当前绘制开关默认关闭，调用方不启用覆盖层；成功的源视频重导出用于保留原时间并生成可播放视频。因此该名称不能推断画面必有骨架或框。骨架叠加走独立 pose-preview 数据接口，在前端按播放时间绘制。

### 7.5 推理 runtime

合同允许 pytorch/onnxruntime/tensorrt 等枚举，但当前两个姿态后端只实际支持 pytorch，其他 runtime 被拒绝。枚举预留和可执行实现必须分开。

### 7.6 动作与触球

FS01/FS02/FS09 是姿态运动候选；底线/发球/接发也是规则或候选识别与阶段分析，不应自动计为确认触球。球拍检测 bbox 不提供真实触球时刻或拍面三维角度。

### 7.7 “实时”与“取消”

视频是上传后异步离线分析；运行中可轮询已写产物，并非摄像头直播协议。浏览器 AbortController 取消请求或观察不等于停止服务器 GPU 工作；数据库无 cancelled 状态，当前也无取消任务 API。

### 7.8 旧报告与新算法

current/legacy-v1 是响应表示，不会改写已有原始测量；旧坐标合同标记待更新，不能把旧数值直接重新命名成新物理含义。历史动作候选可从旧 frames 派生更新，但改变 pose 网络或基础坐标提取通常需要重跑对应分析。

## 8. 代码中的三种“保证”

**结构保证**来自 schema、Python 验证器、TypeScript guards、白名单与有限数检查，防止不合法字段、NaN、错类型、错身份流入后续环节。

**来源保证**来自视频/帧/规则/注册表 hash、事件人物绑定、runtime profile、可信晋升记录，防止把不同模型、不同事件、不同规则版本的数据拼接使用。SHA-256 能识别内容，不自动认证标注者身份或证明数据真实。

**业务语义保证**来自 measurement/scoring 分离、缺测 null、不自动补等级、候选不算触球、参考分不称技术分。这类保证即使 JSON 完全合法也仍然需要专门逻辑和测试。

模型准确率则需要独立真值与实验评价。结构校验通过、代码测试通过、流水线成功，都不是识别准确率通过。

## 9. 正确阅读代码的顺序

第一遍沿实际用户流程：`app/page.tsx` → `ScoreLab.tsx` → `resumable-upload.ts/api-client.ts` → 网关 → `service/api.py` → `database.py/worker.py` → `vision/pipeline.py`。目的：理解请求如何走完全程。

第二遍沿数据生成：`vision/inference.py` 与 pose 后端 → `vision/tracking.py` → `tracking/primary_player.py` → `events/rules.py` → `features/coordinates.py`、平滑、事件特征 → `scoring/loop.py`。目的：理解一个数值从哪来。

第三遍沿结果解释：`scoring/quality_policy.py/scoring.py` → `service/training_evaluation.py` → `user_demo.py/analysis_report.py` → `LiveResults/TrainingReportOverview/MeasurementEvidence`。目的：理解数值何时能用、怎样在页面解释。

第四遍再读离线闭环：annotation → evaluation → calibration_dataset/fitting/independent_test/promotion → training。目的：理解如何用真值发现误差，以及如何将新模型或新评分能力安全接入既有合同。

不建议按 scripts 文件名从 M66 一直顺读到 M97；先找它导入的 src 模块，再看它固定了什么实验协议和输入输出，会更清楚。

## 10. 需求改动会穿过哪些层

### 10.1 换姿态模型

会涉及 deployment-presets、pose backend/adapter/topology、模型配置与权重、运行 profile 与 provenance。后续 feature 的有效性、所需关节和模型误差分布可能变化，所以还需要对照固定帧与事件的评估，并检查已有校准是否允许该 runtime profile。仅替换 .pth 路径不能推出旧等级阈值仍有效。

### 10.2 新增动作或指标

动作检测涉及候选/事件边界；测量涉及 required joints、坐标单位、时间窗口、特征公式与缺失理由；指标注册涉及当前 lifecycle authority 与 requirements；用户报告还要有标签、解释、证据映射与前端 guards。若要正式分，则另需真值、校准、测试与晋升。单加一张 catalog 卡只会扩展目录展示。

### 10.3 改参考分

权重与聚合在 service/training_evaluation，不在旧前端 scoring/engine。必须同步 score_explanation、相关语义声明与测试。否则页面可能展示一个公式计算的分数，却用另一份解释。

### 10.4 改回放或轨迹显示

若只改叠加样式与交互，多数在网页 playback/viewer 层；若改坐标/时间含义，则必须同时检查源 PTS、pipeline 时间窗、pose-preview 与导出合同。只给前端加一个 time offset 可能掩盖源时间证据不充分的问题。

### 10.5 改异步或多 worker

需要同时看 SQLite claim/续租/完成写条件、GPU模型常驻对象、输出目录重入、CPU线程数和视频资源。当前设计以单节点单 worker 为主要目标；API 多进程不是推理并行方案。

## 11. 验证记录

本次为代码逻辑梳理，完成了 Git 文件全量索引、Python AST 符号提取、主链路与关键分支交叉阅读。索引不动态 import 每个模块，以免把模块导入副作用误当只读操作。

为核实参考分与解释逻辑，实际运行：

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.test_training_evaluation tests.test_score_explanation
```

结果：27 个测试通过，测试运行时间 0.476 秒。只代表这两个测试模块的规则验证；没有运行 tests 目录的完整测试集（164 个 Python 文件和 3 个 JS/TS 文件）或 24 文件网页测试集，也没有重新进行 GPU 推理、真实视频准确率评测或云端部署验证。

整个项目的回归入口 `scripts/run_regression_suites.py` 会区分 runtime 与 historical 依赖，默认 all 才是完整回归；不能把 runtime 子集通过称为所有历史测试通过。网页 npm test 会先 build，再用 Node test runner 执行测试，还另有 typecheck 和 lint。

## 12. 如何从异常定位到代码

- 文件上传不上去：先看 resumable-upload 与网关，再看 UploadStore manifest、chunk digest、状态码、空间与 TTL。
- 已入队一直等待：看 jobs.status、worker 进程与模型初始化；API ready 不能证明 worker 消费正常。
- 推理中途失败：看 process_one 捕获的异常、lease 与最近 progress，再按 pipeline phase 找对应产物阶段。
- 有视频但没有动作：看 frames 中的人物/关键点、主球员连续性、真实时间、镜头校正，再看事件/动作候选筛选，不能先假设评分坏了。
- 有动作但指标缺失：看必需关节、时间窗、feature.valid、quality gate 和 scoring context；局部单项测量可能仍然有效。
- 有数值却没有 A–E：先查 F2/F4、校准与可信绑定；这通常是预期门控。
- 老任务展示变化：看动作候选版本懒更新和 report_contract；区分派生响应变化与原始文件被改写。
- 网页显示不完整：看 current/legacy 合同、前端解析守卫、truncate/scope 字段以及已移除的 UI 分支。
- 播放与骨架错位：看 source PTS、分析窗口、回放起点和 pose-preview 的源视频时间坐标，不能用帧号/FPS 简单代替所有视频时间。

上面的定位路线分别对应分篇中的具体函数和源码行号，可以作为后续开发与排错的入口。
