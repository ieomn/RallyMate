# RallyMate 接口与端到端链路（当前态）

> 文档口径：2026-09-05 源码当前态  
> 文档修订：1.2.0  
> HTTP 服务：`RallyMate Vision API` v1.2.0  
> 主要实现：`src/rallymate_service/api.py`、`database.py`、`worker.py`、`user_demo.py`  
> 配套图：`docs/diagrams/RallyMate完整系统架构_当前态_v1.0.drawio`  
> 本次修订：补充首次接入所需的评分概念、状态语义与结构化示例；未变更 HTTP 路由、响应字段或运行逻辑。

## 1. 执行摘要

RallyMate 当前已经形成一条可运行的本地视频分析链：用户可按选择顺序提交一个或多个视频，SQLite 持久化任务，Worker 对每个任务执行人物检测、RTMPose、主球员跟踪、FS01/FS02/FS09 事件与 13 项 F2 指标测量，随后生成报告、可选视频和结构化产物。成功任务可通过 Demo v1.2 用户结果接口查看“动作表现参考分（Beta）”、严格 13 项自然语言观察/证据/训练建议和 GPU/CPU 运行信息；刷新页面后可恢复已提交任务的轮询状态。

这里必须区分两件事：

- **Beta 训练评价** `training_evaluation` 根据本视频的可测比例、必需特征覆盖、骨架置信信息、跨片段重复性和评分证据质量生成，只用于训练复盘。分数缺失时保持 `null`，用户页显示“暂无法评价”，不显示 0。
- **正式评分**仍受 F4、人类真值、独立测试、标定资产、可信晋级账本和运行时精确绑定共同约束。当前 13 项指标均为 F2，`formal_scoring.available=false`，正式分和 A～E 为 `null`；Beta 分不得冒充教练标定分。
- **v1.1 兼容历史** `final_demo_score`、`analysis_quality`、`display_score` 与 `formation_assessment` 仍在响应中，但不再是普通用户页的技术表现主展示。

当前 HTTP API 有 **9 个显式业务路由**。`/assets/*` 是静态挂载，`/docs`、`/redoc`、`/openapi.json` 是 FastAPI 自动路由，均不计入这 9 个。

### 1.1 评分概念与接口语义（首次接入必读）

RallyMate 把模型预测、可测特征、证据质量、Beta 训练反馈和正式教练评分视为五个不同的合同层。调用方必须按字段所属层解释结果，不能因为某个字段含有 0～100 数字，就把它解释为准确率、教练分或正式跨指标总分。

#### 真值、独立测试与正式聚合的定义

1. **事件真值（event ground truth）**：由合格标注者直接观察原视频，针对确定的源视频、目标球员和事件代码，人工给出 `start_ms`、`end_ms` 以及 `key_phases_ms`，并经过独立标注和必要的裁决。自动生成的 `events.jsonl` 记录只是事件候选；候选数量、边界置信度和模型间一致都不能替代事件真值。
2. **关键点真值（keypoint ground truth）**：在指定帧、时间戳和目标球员上，由人工校正人体关键点二维坐标，同时记录可见、遮挡或不可标注状态。它用于计算关键点误差以及后续特征误差。RTMPose 输出的坐标与 confidence 是模型预测，不是关键点真值；两个 Pose 模型给出相近结果也不构成真值。
3. **多教练 A～E 真值（multi-coach A–E ground truth）**：多名具备资格的教练依据冻结的指标定义和等级量表，对同一事件实例、同一 `indicator_id` 独立给出 A～E 有序类别，再记录标注者一致性、分歧裁决和来源版本。评分卡中的等级文字、单名教练意见或模型生成的训练建议都不能替代这类真值。A～E 是有序类别，不得在没有预注册映射时直接按 A=5、B=4 等方式求平均。
4. **独立测试集（independent test set）**：在训练、特征选择、阈值选择和标定拟合期间保持封存，并按运动员、源视频和采集来源与训练/调参与标定集合隔离的数据集。模型、registry、特征版本、标定资产、质量门禁和评测协议冻结后，才允许在该集合上执行一次预注册评测。开发视频重放、同帧模型比较或调参后反复查看的集合都不是独立测试集。
5. **跨事件/跨指标总分（formal aggregate）**：未来若提供正式总分，必须先让每个事件实例上的单指标结果通过 F4、可信标定和独立测试并进入 `scored`，再定义版本化的实例聚合、指标权重、事件族权重、最低覆盖率、缺失值处理和总分到等级的映射。设某动作族内已获授权的正式数值为 `s[e,i]`、指标权重为 `w[i]`，动作分可定义为 `A[e] = Σ(w[i] × s[e,i]) / Σw[i]`；再以事件族权重 `v[e]` 定义 `T = Σ(v[e] × A[e]) / Σv[e]`。这只是正式合同必须明确的数学形态，不是当前已实现公式；缺失指标不能在没有覆盖率规则时被静默剔除，字母等级也不能直接代入该公式。

当前 `training_evaluation` 使用的是另一套明确标为 Beta 的参考聚合：每个严格指标根据可测实例比例、必需特征覆盖、中位特征置信、跨片段重复性和评分证据比例生成可空参考分；`performance_assessment.score_0_to_100` 是该动作族内非空单项参考分的无权重平均，`training_evaluation.score_0_to_100` 是 13 项中非空单项参考分的无权重平均。`evaluated_indicator_count/total_indicator_count` 必须与分数同时读取。该算法允许在部分单项可用时形成 Beta 反馈，但不满足上述正式跨事件/跨指标总分定义。

#### 当前接口字段所属层级

- **测量与推理证据**：`actions[].detected_segments`、`measured_indicator_count`、`measurement_coverage_percent`、`amplitudes`，以及 `indicator_evaluations[].measured_instance_count`、`total_instance_count` 和 `representative_measurements`。这些字段来自模型候选事件和可测特征，不是真值，也不直接证明准确率。
- **证据质量**：`indicator_evaluations[].components` 中的可测比例、特征覆盖、置信、重复性和评分证据比例，`limitations_zh`，以及兼容字段 `analysis_quality`。产物层的 `quality_gate`、`reason_codes` 和 `feature_status` 提供更细的阻断依据。证据质量回答“当前材料能否支持计算或参考判断”，不回答“球员技术是否优秀”。
- **Beta 参考分**：`training_evaluation.score_0_to_100`、`actions[].performance_assessment.score_0_to_100`、`indicator_evaluations[].score_0_to_100`，以及对应的 `level_zh`、`summary_zh`、`observation_zh`、`suggestion_zh`、`strengths_zh` 和 `priorities_zh`。这些字段用于本次训练复盘，`is_formal_coach_score=false`。
- **正式字段**：当前 `formal_scoring.available=false`、`formal_scoring.score_0_to_100=null`、`formal_scoring.grade=null`、`formal_scoring.status="calibration_required"`；`training_evaluation.formal_grade` 和每个 `indicator_evaluations[].formal_grade` 也必须为 `null`。当前实现不得从 Beta 分、`final_demo_score`、`analysis_quality`、`display_score` 或模型 summary 中推导这些正式字段。
- **兼容历史字段**：`final_demo_score`、`analysis_quality`、`display_score` 与 `formation_assessment` 为 v1.1 客户端和证据追溯保留。它们分别描述信息成型或材料完整程度，不属于当前技术表现主分，也没有正式教练评分语义。

#### `null`、状态字符串、布尔可用性与数字 0

- JSON `null` 表示当前没有可发布的该字段值。客户端应保留空值并显示“暂无法评价”或“—”，不得转换为数字 0、E 级或失败分。
- `available=false` 是容器级可用性声明。例如当前 `formal_scoring.available=false` 表示正式评分能力未开放；Beta 总评的 `training_evaluation.available=false` 表示 13 项中没有任何非空单项参考分。
- `feature_status="measured"` 表示某一事件实例的必需测量特征已计算，不表示事件边界正确、特征准确或正式评分可用；它可以与评分层 `status="unavailable"` 同时出现。
- `status="unavailable"` 表示该实例的特征输入、必需阶段、身份连续性、评分上下文或质量门禁不足。调用方应读取 `reason_codes` 或限制说明，不能将它概括成“模型得了 0 分”。
- `status="calibration_required"` 表示测量和当前评分证据门禁已经允许继续，但缺少获批的教练标定、独立测试或可信发布授权；此时 `grade` 仍为 `null`。它不是比 `unavailable` 更高或更低的技术等级。
- `status="scored"` 只表示事件级单指标已经使用受信、版本化且运行时绑定的正式标定生成 A～E。当前 13 项均为 F2，因此当前真实运行不应出现这一状态；即使未来某个单指标为 `scored`，也不会自动产生动作总分或跨事件总分。
- 数字 `0` 只在字段合同明确允许且计算实际得到零时有效，例如计数为 0，或一个 `available=true` 的数值评分确实为 0。`null`、`unavailable`、`calibration_required`、缺帧、遮挡和没有检测到候选都不得用 0 填充；当前正式 `score_0_to_100` 必须为 `null`，不是 0。

#### FS01 从视频证据到结果字段的结构化示例

以下内容是字段关联示例，不是某个端点原样返回的单一 JSON。时间和 Beta 分数仅用于说明结构，不是当前任务验收结果、教练阈值或正式标定参数。

```yaml
video:
  video_id: example-video-sha-bound
  segment_ms: [12480, 13160]

event_candidate:                 # 来源：events.jsonl；自动候选，不是事件真值
  event_id: fs01-example-001
  event_code: FS01
  start_ms: 12480
  end_ms: 13160
  key_phases_ms:
    preload_ms: 12560
    takeoff_proxy_ms: 12720
    landing_proxy_ms: 12920
    redistribution_ms: 13000
    initiation_ms: 13120

pose_prediction:                # 来源：frames.jsonl；模型关键点，不是关键点真值
  backend: RTMPose
  native_format: Halpe26
  observed_points: [hip, left_knee, right_knee, left_ankle, right_ankle]

feature_record:                 # 来源：indicator-features.jsonl
  event_id: fs01-example-001
  indicator_id: FS01-M02
  feature_status: measured
  features:
    - {feature_name: hip_center_y_body, value: 1.55, unit: body, valid: true}
    - {feature_name: left_knee_flexion_deg, value: 108.4, unit: deg, valid: true}
    - {feature_name: right_knee_flexion_deg, value: 111.2, unit: deg, valid: true}
    - {feature_name: stance_width_body, value: 0.63, unit: body, valid: true}
    - {feature_name: hip_center_relative_to_ankle_support, value: 0.91, unit: ratio, valid: true}
  scoring_status: calibration_required
  grade: null

demo_result:                    # 来源：GET /v1/jobs/{job_id}/demo-result
  indicator_evaluation:
    indicator_id: FS01-M02
    score_0_to_100: 74           # Beta 单项参考分；示例值
    measured_instance_count: 1
    total_instance_count: 1
    observation_zh: "根据当前视频证据形成的自然语言观察"
    suggestion_zh: "根据当前视频证据形成的训练建议"
    formal_grade: null
    is_formal_coach_score: false
  action:
    event_code: FS01
    performance_assessment:
      score_0_to_100: 74         # FS01 非空 Beta 单项分平均；本例仅一项非空
  training_evaluation:
    score_0_to_100: 74           # 全部非空严格单项分平均；本例仅一项非空
    evaluated_indicator_count: 1
    total_indicator_count: 13
    formal_grade: null
    is_formal_coach_score: false
  formal_scoring:
    available: false
    score_0_to_100: null
    grade: null
    status: calibration_required

future_formal_aggregate:         # 当前没有此响应对象
  prerequisites: [event_ground_truth, keypoint_ground_truth, multi_coach_A_to_E_truth, sealed_independent_test, F4, trusted_promotion]
  indicator_grade: null
  action_score: null
  cross_event_total: null
```

这条示例链中的关键关系是：视频先产生事件候选和阶段，指定时间段内的关键点预测再派生特征；特征按 `event_id + indicator_id` 进入单项评价，多段同类动作共同影响该单项的可测比例和重复性，单项再进入动作级与 Beta 总评。事件真值、关键点真值和多教练 A～E 真值是未来验证和标定的外部监督证据，不能由链路前面的模型输出自我证明。

#### Ball、Racket、Court 和 298 项的当前范围

指标 registry 共 298 项，其中 **GS（底线基础事件）248 项、FS（步伐事件）50 项**。`GET /v1/model-capabilities` 返回这份静态业务能力矩阵和依赖审计；“在 registry 中存在”只表示已登记指标定义，不表示运行时已经实现测量、通过真值验证或能够正式评分。

当前最小闭环只包含 13 个 Pose 依赖的 F2 指标：FS01-M02～M05、FS02-M02～M05、FS09-M01～M05，数量为 4+4+5。Ball、Racket 和 Court 当前可在逐帧产物中提供通用检测框、轨迹线索或场地提示，但这些输出没有进入上述 13 项的注册依赖与 Beta 聚合。通用 `sports ball` 框不能等同稳定的比赛球轨迹，球拍矩形框不能等同拍头、拍面或甜区关键点，`court_mode=auto` 的场地提示也不能等同经过版本化验证的场地坐标标定。

GS 指标以及其余 FS 指标中，凡是依赖活动球轨迹、击球接触、球拍专项关键点、拍面方向或场地坐标变换的项目，都还需要相应专项模型、真值、误差评测和生命周期晋级。仅仅在同一视频中检测到 Ball、Racket 或 Court 不能把这些指标加入 Demo 结果；普通用户页和 `training_evaluation` 都以严格 13 项白名单为边界，额外 `indicator_id` 不参与卡片展示、动作分或 Beta 总分。

## 2. 服务边界与通用约定

### 2.1 基础地址与数据格式

- 本地示例地址：`http://127.0.0.1:8000`
- 普通响应：UTF-8 JSON。
- 上传请求：`multipart/form-data`。
- 用户页面：UTF-8 HTML；其 CSS/JavaScript 从同源 `/assets/*` 加载。
- 时间字段：SQLite 作业层使用带 UTC 偏移的 ISO 8601 文本；推理产物中的时间轴以源视频 `timestamp_ms` 为准。
- 作业 ID：服务端生成 UUID 字符串。客户端不能指定，也没有幂等键。

### 2.2 认证

当 `RALLYMATE_API_KEY` 未设置时，受保护路由不要求认证；设置后，必须发送精确的请求头：

```http
Authorization: Bearer <RALLYMATE_API_KEY>
```

服务使用恒定时间比较检查完整 Bearer 字符串。缺失或错误时返回 `401`，正文 detail 为 `missing or invalid bearer token`。

始终公开的当前路由：

- `GET /`
- `GET /health/live`
- `/assets/*`

其余 7 个显式业务路由按上述可选 Bearer 规则保护。FastAPI 自动生成的 `/docs`、`/redoc`、`/openapi.json` 当前也没有额外全局鉴权；对非本机部署，应在反向代理层限制访问，或后续改为全局依赖。

### 2.3 当前没有的协议能力

- 没有 CORS 中间件；当前用户页面设计为同源调用。
- 没有 SSE、WebSocket 或回调；客户端轮询任务状态。
- 没有任务取消、删除、重排或重试 HTTP 接口。
- 没有上传幂等键、分页游标或租户字段。
- 没有把 M93 人工标注工作台暴露为主服务 HTTP API；该链目前是本地 Python API 加受限 loopback 工作台。

## 3. 九个显式路由总表

| # | 方法与路径 | 鉴权 | 成功状态 | 用途 |
|---:|---|---|---:|---|
| 1 | `GET /health/live` | 否 | 200 | 仅证明 Web 进程可响应 |
| 2 | `GET /health/ready` | 可选 Bearer | 200 | 检查模型、注册表、标定依赖与队列状态 |
| 3 | `GET /` | 否 | 200 | 返回面向用户的视频上传与结果页面；不进入 OpenAPI |
| 4 | `GET /v1/model-capabilities` | 可选 Bearer | 200 | 返回 298 项静态能力和当前 13 项最小测量闭环 |
| 5 | `GET /v1/jobs` | 可选 Bearer | 200 | 按创建时间倒序列出最近任务 |
| 6 | `POST /v1/jobs` | 可选 Bearer | 202 | 上传并持久化一个异步视频分析任务 |
| 7 | `GET /v1/jobs/{job_id}` | 可选 Bearer | 200 | 查询单个任务、进度、摘要和产物地址 |
| 8 | `GET /v1/jobs/{job_id}/demo-result` | 可选 Bearer | 200 | 把真实测量转换为用户可读结果，不生成虚假等级 |
| 9 | `GET /v1/jobs/{job_id}/artifacts/{artifact_name}` | 可选 Bearer | 200 | 预览或下载白名单中的既有成功产物 |

## 4. 路由详解

### 4.1 `GET /health/live`

用途：进程存活检查，不检查数据库、模型或 GPU。

```json
{"status":"ok"}
```

### 4.2 `GET /health/ready`

用途：部署就绪检查。即使未就绪也返回 HTTP 200，调用方必须读取 `status` 和 `reasons`。

检查项包括：

- 模型许可证声明是否合法；
- SQLite 是否可初始化；
- registry lifecycle 是否能解析出唯一 `runtime_feasibility` 权威工件；
- Detect、Pose checkpoint 和可选 Pose config 是否存在；
- 若配置生产标定资产：资产、可信晋级账本、运行 Profile 绑定、机位证据目录及其契约是否完整。

响应关键字段：

```json
{
  "status": "ready",
  "reasons": [],
  "queue": {"queued": 1, "running": 0, "succeeded": 3},
  "environment": "development",
  "model_license_ack": "development",
  "pose_preset": "rtmpose-m-halpe26-online",
  "pose_backend": "rtmpose",
  "pose_profile": "realtime",
  "pose_native_keypoint_format": "halpe26",
  "registry_lifecycle": {
    "authority_version": "...",
    "manifest_sha256": "...",
    "role": "runtime_feasibility",
    "artifact_sha256": "..."
  }
}
```

`registry_lifecycle` 在权威解析失败时为 `null`。`queue` 只包含当前数据库中实际出现的状态键。

### 4.3 `GET /`

返回 `src/rallymate_service/assets/user-demo.html`。页面提供：

- 多视频选择、拖放与上传，并严格按选择顺序逐个调用现有单任务接口；
- 本地 Bearer token 输入；
- 当前任务阶段与进度轮询、批次 X/Y 进度及已完成结果切换；
- 刷新后从 localStorage 恢复已提交任务 ID 并继续轮询；
- 通过 `/?job_id=<UUID>` 直接恢复并展示一个已有任务；只接受 UUID，并仍只保存不敏感的 job ID；
- `training_evaluation` 动作表现参考分（Beta）、优势和优先训练项；
- FS01 4 项、FS02 4 项、FS09 5 项共 13 项卡片，以及每类动作的 `performance_assessment`；
- 每项指标的自然语言评分摘要、观察、证据、训练建议和限制；分数 `null` 时显示“暂无法评价”；
- GPU/CPU、设备、RTMPose backend/profile 和 CPU 线程上限运行标识；
- 默认不生成标注视频；用户显式开启且产物存在时只提供骨架视频链接。

普通用户页不展示原始任务/结果 JSON，也不提供 scoring-loop 或 analysis 内部报告链接。HTTP/code 和任务失败
会转换为友好中文，不直接显示 `job.error` 或异常正文。

页面通过 `/assets/user-demo.css?v=1.2.0` 与 `/assets/user-demo.js?v=1.2.0` 加载静态资源，以避免浏览器继续
使用 v1.1 缓存；查询参数只用于缓存版本，不新增路由。

页面不依赖外部 CDN，也不会把视频发送到 RallyMate 服务之外。localStorage 键为
`rallymate-demo-batch-v1`，只保存批次版本、已提交/已完成/失败任务 ID、当前任务 ID、当前序号和总数；
不会保存 token、认证头、视频字节、File 对象/文件名或结果正文。刷新后需要重新输入 token，尚未提交的本地文件需要重新选择。

### 4.4 `GET /v1/model-capabilities`

返回 `static_model_capability()` 生成的静态 298 项 GS/FS 能力矩阵，并追加当前 lifecycle 权威下的最小测量闭环。

顶层关键字段：

- `schema_version`
- `registry_version`
- `indicator_count`
- `domains`
- `model_granularity`
- `pose_assessment`
- `capability_matrix`
- `minimum_scoring_loop`

`minimum_scoring_loop` 当前包含：权威 registry 路径/版本/哈希与 lifecycle 信息、13 个 indicator ID、每项 F2 成熟度、`calibration_required_without_coach_ground_truth` 等级策略，以及生产标定、可信账本、运行 Profile 绑定和机位证据是否已配置。

### 4.5 `GET /v1/jobs?limit=20`

返回最近任务，按 `created_at DESC` 排序。

- `limit` 必须是整数；FastAPI 类型校验失败为 422。
- 数据库实际将值钳制到 1～100，因此 `limit<=0` 等价于 1，`limit>100` 等价于 100。

```json
{
  "items": [{"id":"...","status":"queued","artifact_urls":{}}],
  "count": 1
}
```

### 4.6 `POST /v1/jobs`

请求类型：`multipart/form-data`。

| 字段 | 类型 | 默认值 | 约束与语义 |
|---|---|---|---|
| `video` | file | 必填 | 扩展名须为 `.mp4/.mov/.m4v/.avi/.mkv`；非空且可解码 |
| `court_mode` | string | `auto` | `auto`、`manual` 或 `disabled` |
| `manual_polygon_normalized` | JSON string / null | null | 4 个 `[x,y]` 点，坐标均在 0～1；manual 模式必填 |
| `manual_polygon_role` | string | `court_outer_doubles_corners` | 或 `visible_region` |
| `write_annotated_video` | boolean | true | 是否生成 `annotated.mp4`；API 为兼容旧客户端保留默认 `true`，Demo v1.2 页面显式传 `false`，用户勾选后才传 `true` |
| `max_players` | integer | 2 | 1～4 |
| `start_ms` | integer | 0 | 不小于 0 |
| `end_ms` | integer / null | null | 若提供，必须大于 `start_ms` |
| `max_frames` | integer / null | null | 若提供，必须不小于 1 |

上传过程按 1 MiB 分块写入。默认最大字节数为 2 GiB，默认最大视频时长为 30 分钟，分别可由 `RALLYMATE_MAX_UPLOAD_BYTES` 和 `RALLYMATE_MAX_VIDEO_DURATION_SECONDS` 修改。安全化后的原始文件名仅用于显示和上游元数据，磁盘文件名使用服务端 UUID。

服务探测视频后生成内部 request JSON，固定当前模型、置信度、`frame_stride=1`、球场刷新策略和 lifecycle 权威 registry；普通上传者不能通过表单指定生产标定账本或替换可信 registry。

成功返回 `202 Accepted` 和公开作业对象。注意字段是 `id`，不是 `job_id`：

```json
{
  "id": "c1c14872-12c0-4f77-8805-5b9ccb31e908",
  "status": "queued",
  "original_filename": "serve.mp4",
  "created_at": "2026-09-04T08:00:00+00:00",
  "updated_at": "2026-09-04T08:00:00+00:00",
  "started_at": null,
  "completed_at": null,
  "attempts": 0,
  "progress": {"phase":"queued","percent":0},
  "error": null,
  "summary": null,
  "artifact_urls": {},
  "demo_result_url": null
}
```

### 4.7 `GET /v1/jobs/{job_id}`

不存在返回 404。存在时返回公开作业对象。

公共字段恒定集合：

- `id`、`status`、`original_filename`
- `created_at`、`updated_at`、`started_at`、`completed_at`
- `attempts`、`progress`、`error`、`summary`
- `artifact_urls`、`demo_result_url`

成功任务额外返回 `scoring_state`。`artifact_urls` 只列出白名单中实际存在的文件；失败或未完成任务返回空对象。`summary` 是完整推理摘要，可能较大。

### 4.8 `GET /v1/jobs/{job_id}/demo-result`

仅成功任务可调用：

- 作业不存在：404；
- 作业尚未 `succeeded`：409；
- 已成功的旧作业缺少 `indicator-features.jsonl`：409，错误码
  `legacy_job_requires_reanalysis`，客户端应提示用户重新上传并分析；
- `indicator-features.jsonl` 存在但为空、不是严格 UTF-8 JSONL、含重复键或非有限数字，或 summary 不是已完成运行：500，detail 以 `demo result is unavailable:` 开头。

返回契约见第 7 节。该接口读取真实 `summary` 与 `indicator-features.jsonl`，不会写入推理产物，也不会生成合成 A～E。

### 4.9 `GET /v1/jobs/{job_id}/artifacts/{artifact_name}`

响应顺序为：先验证产物名在白名单，再检查作业存在、作业已成功、文件实际存在。对应错误分别为 404、404、409、404。响应使用 `FileResponse`，文件名为 `{job_id}-{artifact_name}`。其中 `annotated.mp4`、`analysis-report.html`、`scoring-loop-report.html` 返回 `Content-Disposition: inline`；其余 15 项返回 `Content-Disposition: attachment`。Demo v1.2 普通用户页只链接实际存在的 `annotated.mp4`，不链接两个内部报告；API 的白名单和响应方式没有改变。

## 5. HTTP 错误语义

| 状态码 | 当前触发条件 |
|---:|---|
| 401 | 已配置 API key，但 Bearer 缺失或不匹配 |
| 404 | 作业不存在、产物名不在白名单，或成功目录中没有该文件 |
| 409 | 请求 demo result 或产物时作业尚未成功；或成功的旧作业缺少 `indicator-features.jsonl`，返回 `legacy_job_requires_reanalysis` |
| 413 | 上传字节数或视频时长超过部署限制 |
| 415 | 文件扩展名不在允许集合 |
| 422 | FastAPI 类型校验失败、空视频、无法解码、表单范围错误或手工四边形错误 |
| 500 | 服务端异常，或成功作业不能按严格契约生成 demo result |

任务进入 Worker 后的推理异常不作为原上传请求的 HTTP 500 返回，而是写入任务的 `failed` 状态和 `error` 字段。

## 6. 作业状态与持久化

状态只允许：`queued`、`running`、`succeeded`、`failed`。

```text
POST /v1/jobs
      │
      ▼
   queued ──Worker 原子 claim──▶ running ──成功──▶ succeeded
      ▲                              │
      │                              └────────────▶ failed
      │
      └── lease 过期且 attempts < max_attempts ── running
```

- SQLite 使用 WAL，任务和进度在进程重启后仍可读取。
- Worker 以 `BEGIN IMMEDIATE` 抢占最早任务，避免同一 SQLite 队列被重复领取。
- 默认 lease 为 1 小时，默认最多尝试 2 次。
- running lease 过期且仍有重试额度时回到 queued；额度耗尽时进入 failed。
- Worker 启动时一次加载 Detect 与 Pose 模型并复用，适合单节点单 GPU。
- 进度常见阶段为 `inference`、`finalizing`、`scoring_readiness`、`completed` 或 `failed`。

## 7. 用户结果 `demo-result` v1.2 契约

本节描述实际响应字段；“真值”“Beta 参考分”“正式聚合”和各状态值的严格含义，见前文“评分概念与接口语义（首次接入必读）”。

顶层字段：

| 字段 | 当前值/语义 |
|---|---|
| `schema_version` | `1.2.0` |
| `result_version` | `rallymate-user-demo-result-v1.2.0` |
| `result_kind` | `real_video_training_feedback_preview` |
| `job_id` | 与推理 summary 一致 |
| `status` | `ready` |
| `headline_zh` | 优先按 Beta 训练评价描述；无评价时回退兼容标题 |
| `training_evaluation` | 当前普通用户主展示；Beta 总评、优势、训练优先项和 13 项明细 |
| `final_demo_score` | v1.1 兼容字段；动作信息成型参考分，不再是普通用户页主分 |
| `analysis_quality` | v1.1 兼容字段；材料覆盖意义的分析完成度 |
| `display_score` | v1.1 兼容别名；值等于 `analysis_quality.value_0_to_100` |
| `formal_scoring` | 正式评分是否可用；当前正常结果固定不可用且总分/等级为 `null` |
| `actions` | FS01、FS02、FS09 三张动作卡，含 Beta 动作评估和严格指标明细 |
| `model` | Pose preset 与原生格式 |
| `runtime` | GPU/CPU、设备、Pose backend/profile、CPU 线程上限和标注视频是否生成 |
| `safety` | 结果边界布尔声明 |
| `artifact_urls` | 成功目录中实际存在的白名单产物 URL |

### 7.1 `training_evaluation` Beta 训练评价

`training_evaluation.evaluation_version` 固定为
`rallymate-training-evaluation-beta-v1.0.0`。关键字段如下：

| 字段 | 语义 |
|---|---|
| `available` | 至少一项严格指标形成非空评价时为 `true` |
| `label_zh` | `动作表现参考分（Beta）` |
| `score_0_to_100` | 13 项中非空单项分的平均；无可评价项时为 `null` |
| `level_zh`、`summary_zh`、`meaning_zh` | 用户可读等级、摘要和边界说明 |
| `evaluated_indicator_count` / `total_indicator_count` | 已评价项数 / 固定总数 13 |
| `strengths_zh[]` / `priorities_zh[]` | 优势和优先训练建议 |
| `action_evaluations` / `indicator_evaluations` | 三类动作与严格 13 项评价 |
| `component_weights` | 单项分的组件权重 |
| `formal_grade` / `is_formal_coach_score` | 固定为 `null` / `false` |

单项分组合实测实例比例 30%、必需特征覆盖 20%、中位特征置信 15%、跨片段重复性 25% 和评分证据比例
10%。没有足够可测记录或有效特征时，该单项 `score_0_to_100=null`；动作分和总分仅平均已有非空单项，
没有非空项时也保持 `null`。客户端必须显示“暂无法评价”，不能将空值、`unavailable` 或非数字转换成 0。

这套分数只面向训练复盘，不是教练标定分、动作/识别准确率或正式 A～E。总项数和页面卡片严格来自
FS01 4 项、FS02 4 项和 FS09 5 项；响应或产物中出现其他 indicator ID 时，普通用户页不会展示。

#### v1.1 兼容字段与历史公式

`final_demo_score.value_0_to_100` 范围为 0～100，是 FS01、FS02、FS09 三个
`actions[].formation_assessment.reference_score_0_to_100` 的无权重平均；先对每张卡的参考分取整，再对三张卡求平均、取整并钳制到 0～100。它只表示当前视频中**可辨认动作轮廓与幅度信息的形成程度**，不是球员技术水平、教练评分、识别准确率或正式 A～E。

每张卡的信息形成参考分使用以下四个组成项：

```text
0.30 × recognizable_outline_percent
+ 0.30 × measured_indicator_ratio_percent
+ 0.20 × measurement_coverage_percent
+ 0.20 × amplitude_type_coverage_percent
```

`analysis_quality.value_0_to_100` 也在 0～100，但它独立回答“这次分析材料是否完整”，沿用既有公式：

```text
round(100 × (
  0.30 × 已观察动作族比例
  + 0.45 × 已测量唯一指标数 / 13
  + 0.25 × 三个动作族的平均测量覆盖率
))
```

最终值再钳制到 0～100。`analysis_quality.headline_zh` 的阈值为：85 及以上“较完整”，60～84“基本可用”，低于 60“不足”。

公式中的“已测量唯一指标数”来自 `measured_indicator_ids`，与幅度展示使用同一条记录资格：优先要求
`feature_status="measured"`；仅当 `feature_status` 缺失时，才兼容回退检查
`scoring_feature_status="measured"`；同时 `quality_gate.measurement_allowed` 不能显式为 `false`。
该集合也直接决定每张动作卡的 `measured_indicator_count` 和 `measured/partially_measured` 状态。因此被测量门禁明确否决的记录不会增加动作卡可测数，也不会增加分析完成度中的“已测量唯一指标”分量。

`final_demo_score`、`analysis_quality` 和 `display_score` 在 v1.2 中都不再是普通用户主展示分。
`display_score` 只为旧客户端保留，并通过
`compatibility_alias_for="analysis_quality"` 明示为兼容别名，其值必须与
`analysis_quality.value_0_to_100` 相同。三个兼容字段的 `is_formal_technique_score` 都不能被解释为正式
技术分，M96 v1.1 的原始合同和字段记录继续保留，不回写为 v1.2 证据。

### 7.2 动作卡、`performance_assessment` 与严格 13 项

每张卡包含：

- `event_code`、`name_zh`
- `status`: `not_observed`、`partially_measured` 或 `measured`
- `detected_segments`
- `registered_indicator_count`
- `measured_indicator_count`
- `measurement_coverage_percent`
- `amplitudes`
- `performance_assessment`
- `indicator_evaluations`
- `formation_assessment`
- `summary_zh`、`feedback_zh`

`performance_assessment` 包含可空 `score_0_to_100`、`level_zh`、`summary_zh`、
`evaluated_indicator_count` 和 `total_indicator_count`。`indicator_evaluations[]` 每项固定包含
`indicator_id`、`event_code`、`name_zh`、`definition_zh`、可空分数、等级、摘要、
`observation_zh`、`suggestion_zh`、实测/总实例数、组件、组件权重、代表性测量、限制、
`formal_grade=null` 和 `is_formal_coach_score=false`。普通用户页直接把观察、证据和训练建议渲染成自然语言，
不要求读取 raw JSON。

当前动作族与严格指标数为 FS01=4、FS02=4、FS09=5，共 13 项；未知 ID 不进入动作卡。动作段数优先取
summary 中的 `event_counts`，若为 0，则按 indicator records 中唯一 `event_id` 回退计数。单项或动作分
为 `null` 时前端显示“暂无法评价”，绝不显示 0。

v1.1 兼容字段 `formation_assessment` 仍含 `status`、`label_zh`、`reference_score_0_to_100`、
`meaning_zh`、`explanation_zh`、`components`、`component_weights` 和 `score_version`。
未观察到片段时状态为 `not_observed`；已观察到且参考分不低于 85 时为
`well_formed_information`，60～84 为 `partially_formed_information`，低于 60 为
`limited_information`。这些状态只描述信息形成程度，不评价动作好坏。普通用户页面直接展示
`summary_zh`。该字段描述信息成型程度，不是当前 Beta 表现分，也不是正式动作质量判断。

幅度使用同一条记录资格：优先要求记录级 `feature_status="measured"`；仅当该状态字段缺失时，才兼容回退要求 `scoring_feature_status="measured"`；并且 `quality_gate.measurement_allowed` 不能显式为 `false`。通过资格后优先读取 `features`；仅在 `features` 缺失时才回退读取 `scoring_features`。子特征即使自报 `valid=true`，只要所属记录未通过上述资格，就不会进入用户展示。

同一动作事件可能在多个指标记录里携带同名特征。适配器先按 `(event_id, feature_name)` 去重，并对该事件内的重复有效值取中位数；再对不同事件的值取中位数形成展示摘要。因此 `sample_count` 表示贡献该幅度的**唯一事件数**，不是重复指标行数。

- FS01：双脚提起幅度、双脚动作时差、落地支撑宽度；
- FS02：第一步位移幅度、启动脚峰值速度、启动持续时间；
- FS09：身体中心减速幅度、左膝屈曲变化、稳定维持时间。

身体尺度相关位移会转换为 `% 身体尺度`；这些值没有教练阈值，不应直接解释为“好/坏”。`unavailable`、measurement hard-failed 或缺少非空 `event_id` 的记录不会显示幅度。

### 7.3 多文件顺序队列与刷新恢复

浏览器允许一次选择多个文件，但后端仍维持一个文件对应一个 `POST /v1/jobs` 的单任务 API。
页面严格按用户选择顺序逐个提交、轮询并读取 `demo-result`，显示一基的 X/Y 批次进度，保留已完成摘要并允许切换查看。这个前端队列没有增加 HTTP 路由。

页面默认取消勾选“生成骨架标注视频”，因此常规提交显式发送 `write_annotated_video=false` 以减少 CPU
编码开销；用户勾选后才发送 `true`。API 表单默认值继续为 `true`，这是旧客户端兼容行为，不应被描述为
v1.2 页面默认。

刷新恢复只基于 localStorage 白名单中的任务 ID 和批次序号。页面会重新读取已提交任务状态，对活动任务继续轮询，对已完成任务重新抓取结果；token 必须重新输入，未提交文件必须重新选择。已成功但缺少 `indicator-features.jsonl` 的旧任务按第 4.8 节返回 409
`legacy_job_requires_reanalysis`，不能伪装成服务器 500 或无结果成功。

页面也接受 `/?job_id=<UUID>`。JavaScript 只在值通过 UUID 格式校验后，把它作为单任务 ID 写入上述非敏感
恢复状态并立即查询；URL 不携带 token、文件名、视频内容或结果 payload。若部署启用了 Bearer，仍需用户
自行输入 token。

### 7.4 正式评分字段

`formal_scoring` 在 Demo v1.2 中固定为不可用：

```json
{
  "available": false,
  "score_0_to_100": null,
  "grade": null,
  "status": "calibration_required",
  "message_zh": "正式教练标定分与 A～E 等级尚未启用；上方 Beta 动作表现分和自然语言建议可直接用于本次训练复盘。"
}
```

Demo v1.2 不暴露正式跨事件/跨指标总分，也不暴露 A～E；即使输入 summary 中出现非空
`scoring_state.grade`，适配器也不会把它提升为用户可用的正式评分。`available` 固定为 `false`，
`score_0_to_100` 与 `grade` 固定为 `null`。未来若产品定义正式聚合评分，必须另行设计合同并经过 F4、
真人真值、独立测试和可信发布验证，不能复用 Beta `training_evaluation`，也不能复用 v1.1 兼容字段
`final_demo_score`、`analysis_quality` 或 `display_score` 冒充。

### 7.5 `runtime` 与执行设备

`runtime` 包含：

- `accelerator`：`GPU` 或 `CPU`；
- `device_name`、`device_used`：用户可读设备名与底层设备值；
- `pose_backend`、`pose_profile`：当前 Pose 后端与运行 profile；
- `cpu_thread_limit`：本次运行记录的 CPU 计算池线程上限；
- `annotated_video_generated`：本次是否实际生成标注视频。

当前默认 preset 是 GPU 优先的 RTMPose-M Halpe26 realtime；CUDA 不可用时回退 CPU。服务配置默认
`RALLYMATE_CPU_THREADS=4`，Worker 启动脚本的显式 `-CpuThreads` 优先于环境变量。Worker 在模型加载前
设置 Torch intra/inter-op、OpenCV、OpenMP、MKL、OpenBLAS 和 NumExpr 计算池；该值不是 FFmpeg 等
外部组件的整个进程总线程数承诺。

旧成功任务的 summary 可能早于 `cpu_thread_limit` 记录字段，因此适配结果允许该值为 `null`；新任务会把
实际配置写入 summary，默认应为 4。客户端不能把历史 `null` 猜成 4，也不能用它判断旧任务实际线程数。

### 7.6 当前本机真实验收快照

成功任务 `d7c617d6-89d2-44f2-83e5-de25cb24b689` 已由当前 v1.2 适配器重放验证：总分 78/100，
`evaluated_indicator_count/total_indicator_count=13/13`，动作分 FS01=78、FS02=77、FS09=78；运行设备为
GPU `NVIDIA GeForce RTX 5070 Ti`，Pose backend/profile 为 RTMPose/realtime。该任务是在线程字段落盘前
生成的历史成功任务，所以 `runtime.cpu_thread_limit=null`；它曾显式生成标注视频，不代表普通用户页默认
开启。`formal_scoring` 仍为 `available=false`、正式分与 grade=`null`。这些数字只证明当前合同可从真实
产物形成 Beta 训练反馈，不是准确率、教练标定分或正式 A～E 证据。

## 8. 18 项产物下载白名单

| 文件名 | 内容 |
|---|---|
| `summary.json` | 单次运行总摘要、模型、覆盖率、状态与产物索引 |
| `frames.jsonl` | 逐帧检测、Pose、球场、质量与时间戳 |
| `annotated.mp4` | 可选的可视化标注视频 |
| `preview.jpg` | 标注视频首帧预览（实际生成时才出现） |
| `scoring-readiness.json` | 298 项评分颗粒度审计 |
| `analysis-report.html` | 完整浏览器分析报告 |
| `primary-player.jsonl` | 主球员逐帧时间线 |
| `primary-player-summary.json` | 主球员选择诊断 |
| `events.jsonl` | FS01/FS02/FS09 候选事件与关键阶段 |
| `features.jsonl` | 事件级原始/平滑特征 |
| `indicator-features.jsonl` | 13 项指标的紧凑特征、门禁和状态 |
| `scores.jsonl` | 单事件单指标评分状态；无标定时不生成等级 |
| `event-feature-errors.json` | 事件/特征不可用原因聚合 |
| `scoring-loop-summary.json` | 最小评分循环汇总 |
| `scoring-loop-report.html` | 事件、指标、证据帧和门禁报告 |
| `calculation-readiness.json` | 指标计算准备度 |
| `indicator-measurement-portfolio.json` | 指标测量组合视图 |
| `scoring-cycle-measurement.json` | FS01→FS02→FS09 周期关联测量 |

白名单只决定“可以请求哪些文件名”；响应还要求作业成功且文件实际存在。它不是文件完整性证明。运行结束时 `validate_run_artifacts` 会另外复核跨文件外键、状态、单位和 summary 中的工件 SHA。

展示方式不改变白名单和内容本身：`annotated.mp4`、`analysis-report.html`、`scoring-loop-report.html` 三项以内联响应供浏览器新标签页预览；其余 15 项仍作为附件下载。

## 9. 端到端执行链

### 9.1 在线用户链

1. 用户打开 `/`，按顺序选择一个或多个本地视频，并输入可选 token。
2. 页面严格按选择顺序一次取一个文件，以 multipart 调用现有 `POST /v1/jobs`；后一个文件等待前一个任务完成后再提交。
3. API 校验表单、扩展名、字节数、解码能力和视频时长。
4. 服务生成 UUID，把视频写入 `uploads/`，把不可由用户覆盖的完整请求写入 `requests/`，SQLite 写入 queued。
5. 页面轮询 `GET /v1/jobs/{id}`，并只把任务 ID 和批次序号写入 localStorage 以支持刷新恢复；token、文件和结果正文不落 localStorage。
6. Worker 原子领取任务，进入 running，并复用常驻 Detect/Pose 模型；CUDA 可用时运行 GPU RTMPose，
   否则回退 CPU，计算池默认限制为 4 线程。
7. Pipeline 逐帧执行视频质量、YOLO 人物/球/球拍检测、RTMPose、基础跟踪和球场提示，写 `frames.jsonl`。
8. 主球员模块形成稳定时间线；事件模块形成 FS01/FS02/FS09 候选与阶段。
9. 特征、质量门禁和 13 项 F2 指标测量运行；没有可信标定时状态保持 `calibration_required` 或 `unavailable`。
10. 生成结构化产物、HTML 报告和可选标注视频，执行跨产物校验。
11. Worker 将 summary 写入 SQLite 并置 succeeded；异常则置 failed。
12. 页面调用 `demo-result`，优先展示 `training_evaluation` Beta 总评、三类动作表现和严格 13 项自然语言
    观察/证据/训练建议；`null` 显示“暂无法评价”。页面展示运行设备，只在用户显式开启且产物存在时
    提供骨架视频；v1.1 的 `final_demo_score`、`analysis_quality` 和 `display_score` 仅兼容旧客户端。

### 9.2 数据主键与追溯

```text
job UUID
  → request.job_id / summary.job_id
  → video SHA + frame.index + timestamp_ms
  → detection.track_id / pose.person_track_id
  → primary-player source_track_id
  → event_id + event_code + key phases
  → feature(event_id, feature_name)
  → indicator(event_id, indicator_id)
  → score(event_id, indicator_id)
  → registry + model + calibration lineage
```

### 9.3 评分闭锁链

当前 F2 测量成功不等于可以给分。正式输出还需逐指标完成：人类事件/阶段真值、人工校正关键点、逐视角误差、多人教练标签、标定、封存独立测试、F3/F4 证据、可信晋级账本、运行 Profile 与当前视频机位证据精确绑定。任一项缺失都应 fail closed；各类真值和正式聚合的定义沿用前文“评分概念与接口语义（首次接入必读）”，不得在此链路中降低口径。

## 10. M89～M93 人工事件/阶段 Python API

这些函数从 `rallymate_annotation` 导出，是本地文件型工作流，不属于前述 9 个 HTTP 路由。生成器写新目录并做严格重放；验证器拒绝额外文件、重复 JSON 键、非有限数字、路径逃逸、哈希/版本/角色/时间顺序漂移。

### 10.1 技术交接（M89）

```python
build_scoring_truth_event_handoff(
    source_pack_dir, output_dir, *, video_dir=None
) -> dict

validate_scoring_truth_event_handoff(
    bundle_dir, *, source_pack_dir=None, video_dir=None
) -> dict
```

作用：从受控 source pack 构建只读、便携、loopback-only 的全视频事件交接包，或重放验证包和可选源视频。M89 本身不授权标注写入，也不携带机器事件边界、阶段值、等级或阈值。

### 10.2 本地负责人放行（M90）

```python
verify_scoring_truth_event_authorization(
    *, plan_path, release_record_path, handoff_dir
) -> dict

validate_scoring_truth_authorization_binding(binding) -> None
scoring_truth_authorization_binding_sha256(binding) -> str
```

作用：每次构建时同时验证标注计划、负责人放行记录和 M89 技术交接，不允许用旧的序列化 binding 替代源文件重放。当前授权范围固定为 FS01/FS02/FS09、三个全视频任务、A/B 独立标注、C 独立复核。

事件阶段键由授权契约固定，工作台和 intake 不能自行增删：

- FS01：`preload_ms`、`takeoff_proxy_ms`、`landing_proxy_ms`、`redistribution_ms`、`initiation_ms`；
- FS02：`direction_conversion_ms`、`support_extension_proxy_ms`、`lead_foot_motion_onset_proxy_ms`、`first_step_slowdown_proxy_ms`；
- FS09：`peak_speed_ms`、`deceleration_peak_ms`、`restabilization_onset_ms`、`stable_control_onset_ms`。

### 10.3 A/B 独立标注执行包（M93）

```python
build_scoring_truth_event_execution_bundle(
    *, plan_path, release_record_path, handoff_dir,
    role_slot, output_dir
) -> dict

validate_scoring_truth_event_execution_bundle(
    bundle_dir, *, expected_role_slot=None
) -> dict

validate_scoring_truth_event_annotation_submission(
    submission_path, execution_bundle_dir
) -> dict

scoring_truth_event_manifest_binding_sha256(manifest) -> str
```

- `role_slot` 只能是 A 或 B；角色类型均为 `independent_event_phase_annotator`。
- 两人独立观看三段完整视频，创建事件边界和阶段观察，不能导入对方结果或机器候选边界。
- 每段视频必须显式完成 full-video review；完整复核后允许 0 事件提交。
- 事件包括 `annotation_id/event_id/event_code/start_ms/end_ms/phase_observations/confidence_milli/boundary_uncertainty_ms/notes/annotated_at` 及 revision SHA。
- 阶段值只能是 `observed + timestamp_ms` 或 `unobservable + reason`。

### 10.4 C 裁决执行包（M93）

```python
build_scoring_truth_event_adjudication_bundle(
    *, annotator_a_bundle_dir, annotator_a_submission_path,
    annotator_b_bundle_dir, annotator_b_submission_path,
    output_dir
) -> dict

validate_scoring_truth_event_adjudication_bundle(bundle_dir) -> dict

validate_scoring_truth_event_adjudication_submission(
    submission_path, adjudication_bundle_dir
) -> dict
```

- C 的角色类型是 `independent_event_phase_reviewer`，身份经 trim、Unicode NFKC、casefold 后必须与 A/B 两两不同。
- C 可作出 `accepted_event`、`rejected_sources` 或 `c_added_event` 决策。
- A/B 的每个来源事件都必须有 disposition；三个视频都必须完成 C 级复核，即使来源事件数为 0。
- 裁决输入精确绑定两份执行 manifest、两份原始 submission、revision SHA 和参与者身份。

### 10.5 私有事件/阶段 intake（M93）

```python
ingest_scoring_truth_event_phase(
    *, plan_path, release_record_path, handoff_dir,
    execution_a_bundle_dir, execution_a_submission_path,
    execution_b_bundle_dir, execution_b_submission_path,
    adjudication_bundle_dir, adjudication_submission_path,
    output_dir
) -> dict

validate_scoring_truth_event_phase_intake(session_dir) -> dict
```

作用：在全部源 bundle/submission 通过重放后，以原子 staging + 单次 rename 建立 `PRIVATE` intake，字节精确保留 A/B/C 原始提交，并编译：

- `compiled/manual-events.jsonl`
- `compiled/event-annotations.csv`
- `compiled/full-video-review-completion.csv`
- `compiled/validation-report.json`

intake 的 `person_track_id=1` 与 `view_group=source-view-unclassified` 只是投影默认值，不是人工观察真值。它只形成 event/phase annotation，不授权关键点真值、教练等级、标定、晋级或生产评分。

### 10.6 兼容/后续真值包 API

同一包还导出：

```python
build_truth_pack(...)
compile_truth_pack(...)
ingest_scoring_truth_exports(source_pack_dir, export_paths, output_dir)
validate_scoring_truth_intake(session_dir)
```

它们用于更广义的事件、关键点、语义和教练标签真值包；不是 M93 A/B/C event-phase 主执行链的替代入口。

### 10.7 当前实际状态

仓库已完成上述 API、浏览器工作台、严格验证器和回归测试，但没有替操作者伪造执行事实：

- operator release 实例：0；
- A/B execution bundle：0；
- A/B submission：0；
- C adjudication bundle/submission：0；
- private intake：0；
- 人工 event/phase label：0；
- 正式 A～E：0。

因此文档中的 M93 是**可执行能力**，不是已经收集到真人标签的声明。

## 11. 模型候选、微调与验证路径

当前默认部署 preset 为 `rtmpose-m-halpe26-online`：RTMPose-M、Halpe26、256×192、PyTorch realtime。它是 F2 测量默认，不是 F4 评分模型。

现有可比候选包括：

- `rtmpose-m-halpe26-analysis`：RTMPose-M Halpe26 384×288；
- `rtmpose-l-halpe26-analysis-shadow`：RTMPose-L Halpe26 384×288，已登记为只能显式选择的离线 shadow preset；尚无 RallyMate 真人真值准确率，未晋级 F4，也不改变默认路由；
- `rtmpose-x-halpe26-384x288-m95-shadow`：RTMPose-X Halpe26 384×288，登记在单独的 M95 离线候选注册表；已通过本机 3 帧/4 ROI 加载与输出烟测，但没有真人准确率证据；
- `rtmpose-m-wholebody133-analysis`：RTMPose-M WholeBody133 256×192；
- `yolo-baseline`：COCO17 回滚/粗基线。

请求中 `models.pose_preset` 非空时，它不是展示标签，而是完整部署身份。契约从
`models/rtmpose/deployment-presets.json` 解析权威 preset，并逐项要求 `models.pose` 的绝对路径、
`pose_backend`、`pose_runtime`、`pose_profile`、`pose_config`（包括应为 `null` 的情况）和
`pose_native_keypoint_format` 全部一致；任一错配直接抛出 `ContractError`，不会进入推理。没有
`pose_preset` 的旧请求仍按原有独立字段规则校验。

X384 与 L384 的接口身份不同。L384 已是部署注册表中的显式 shadow preset；X384 只存在于
`models/rtmpose/m95-shadow-candidates.json`，不在 `models/rtmpose/deployment-presets.json`，因此
不能作为 `models.pose_preset` 提交，也没有 API/Worker 启动参数。这样可以在不改写 M94 部署注册表和
默认 M256 的前提下保存候选、checkpoint、config、开发视频与密封 holdout 的精确字段。X384 的机器烟测
见 `reports/m95-rtmpose-x-shadow/smoke-report.json`；它明确不是 HTTP 集成、延迟对比或准确率评测。
这里的 M95 registry 是烟测前冻结的输入/协议快照，其候选 `promotion_status` 保存注册时状态；后验状态
以绑定该 registry SHA 的 smoke report 为准，字段记录会同时保存两者，不原地改写实验输入。

分进程启动时，API 会把自身选择的 preset 及其完整模型配置写入每个新任务，Worker 则按启动 preset
预载模型；因此两端必须显式使用同一个值。L384 Shadow 的正确启动方式是：

```powershell
.\scripts\run_api.ps1 -PosePreset rtmpose-l-halpe26-analysis-shadow
.\scripts\run_worker.ps1 -PosePreset rtmpose-l-halpe26-analysis-shadow
```

不能让默认 M256 API 与 L384 Shadow Worker 混用。省略参数时，两端都保持
`rtmpose-m-halpe26-online`；切换档位应先停止两端，再用同一 preset 成对重启。

M71 在固定的 258 个残差帧和既有事件边界上，对 RTMPose-L 做严格超集路由：完整特征实例从 2,338 增至 2,344，门禁后可测从 2,327 增至 2,333，均为 +6，且增益只出现在三段视频中的两段。这证明局部**可观测性**改善，不证明关键点准确率或应全局替换默认模型。

M96 又在 3 个开发视频的同一 45 帧、同一 ROI 和同一 Halpe26 26 点拓扑上对 M256、L384、X384 做同帧诊断，三者均完成 45/45 帧。高置信点覆盖率分别为 0.734188、0.747863、0.769231，延迟 P50 分别为 9.1244、10.6427、11.7265 ms；这些只是不含真人关键点真值的覆盖/延迟诊断，不是准确率或晋级证据，密封 holdout 未启封。

M97 在相同 258 个残差帧和既有边界上评估 X384：相对 M70 新恢复 4、丢失 0；相对 M71 的 L384 只多恢复 1，却丢失 M71 已恢复的 3 项，净少 2。因此 X 不晋级、不替换 M71 结果，也不改变生产默认 `rtmpose-m-halpe26-online`（M256）。这仍是可观测性/测量恢复比较，不是关键点准确率。

合理的下一条改进路径：

1. 冻结候选、视频、ROI、事件边界和评价协议。
2. 对差异帧执行 A/B 双人关键点标注和 C 裁决，按机位报告必需关节 MAE、P95、PCK。
3. 在同一边界上比较下游特征缺失、误差与回归，而不是比较“点更多”。
4. 如预训练候选仍不足，再用合规授权的网球关键点数据微调；训练/验证/独立测试按视频和运动员隔离，禁止帧级泄漏。
5. 在未参与选择的独立视频上执行预注册门禁，同时检查精度、稳定性、速度、显存和失败率。
6. 当前 L 只登记 shadow preset；只有门禁通过后才登记已验证的版本化 preset 并显式切换默认，同时保留旧 preset 可回滚。
7. Pose 模型晋级与正式评分晋级分开：前者通过也不会自动把 13 项指标从 F2 提升到 F4。

### 11.1 M95 本地微调软件接口

M95 的训练准备链是本地文件型 Python/CLI 接口，不属于前述 9 个 HTTP 路由，也不会由用户上传 Demo
自动调用：

- `audit_pose_finetune_readiness(pack_dirs, governance_csv=...)`：返回
  `pose-finetune-readiness-v1.2.0` 报告；只有双人独立标注、第三人裁决、compiled/source/hash、完整
  Halpe26 训练监督、治理字段、train/val 隔离和密封 holdout 拒绝全部通过，才给出
  `ready_for_dataset_export`；
- `export_mmpose_halpe26_dataset(pack_dirs, governance_csv=..., output_dir=...)`：只接受上述 ready 状态，
  二次审计输入 fingerprint，写出不可覆盖的 COCO train/val、JPEG、image manifest 和
  `rallymate-mmpose-halpe26-dataset-v1.0.0` manifest；
- `prepare_or_run_rtmpose_finetune(dataset_manifest, dataset_manifest_sha256, output_dir, execute=False)`：
  重新验证 manifest、所有 artifact、源视频、拓扑、split、治理、X checkpoint/base config/template 和
  MMPose runtime；默认只创建 dry-run 计划，显式 `execute=True` 且全部门禁通过后才调用 Runner。

对应脚本为：

```powershell
$env:PYTHONPATH = "$PWD\src"
python .\scripts\audit_pose_finetune_readiness.py --pack <truth-pack> --governance <governance.csv>
python .\scripts\export_mmpose_halpe26_dataset.py --pack <truth-pack> --governance <governance.csv> --output <new-dataset-dir>
runtime\rtmpose\.venv\Scripts\python.exe .\scripts\run_rtmpose_finetune.py `
  --dataset-manifest <manifest.json> `
  --dataset-manifest-sha256 <SHA256> `
  --output-dir <new-run-dir> `
  --dry-run
```

当前两个 M95 严格关键点包名义合计 1,976 个**历史标注任务**，接受帧/关节值为 0/0，且未提供治理 CSV；它们不是训练清单、dataset manifest 或已授权训练集，不能作为训练就绪证据。因此 readiness 为 `annotation_required`，后两个入口会在写出训练数据或调用 Runner 前拒绝。当前没有真实 M95 dataset manifest、训练 run、新 checkpoint 或准确率声明。

readiness 先要求每个候选 train/val 帧至少一个可见点、可见归一化坐标在 `[0,1)`，且输入 compiled
validation 状态必须是 `ready_for_keypoint_error_evaluation`。exporter 再使用不可变帧尺寸要求映射像素
落在图像半开边界和 task bbox 内，训练适配器对导出结果重放同一几何合同。这些条件避免导出器标为
ready、适配器又因字段解释不同而拒绝。
train/val 还会同时拒绝 JPEG 原始内容 SHA 重叠和按 MMPose 三通道读取后的解码像素 SHA 重叠，
不同编码的同一画面不能绕过 split 隔离。
治理 split 必须精确写成小写 `train/val`，开发阶段不接受 `test`；所有 `video_id` 的源 path/SHA 全局
唯一，同一 video/frame 也不能被拆在多个 truth pack 中再合并。上述规则在各自适用层按同一口径衔接：
抽帧前规则由 readiness/exporter 复核，抽帧后的内容与几何由 exporter/adapter 复核。

### 11.2 M96 Halpe26 evaluation-only 人工试点

M96 新增一个与训练链彻底隔离的小规模、可实际开始的入口：
`data/annotations/m96-halpe26-development-pilot-v1`。它从 3 个开发视频各确定性选取 8 个、覆盖不同时间段的帧，共 24 帧；每帧要求完整 Halpe26 26 点，因此 A、B 每人各有 24×26=624 个点任务。包内不含任何模型坐标，明确排除 c235 holdout，并固定
`evaluation_only=true`、`training=false`、`dataset_export=false`、`promotion=false`。

当前事实仍是：A/B 人工提交行数为 0，C 裁决行数为 0，准确率为 `null`。治理模板只列必填字段和
`REPLACE_WITH` 占位，不推断真实 consent、subject、session 或 split；开始标注前，数据负责人必须另行填写并审核真实授权信息。

A 与 B 使用不同的伪匿名角色 ID 和独立包，不能查看彼此结果或模型候选。可在不同端口启动只暴露当前角色包和必要视频的固定白名单 loopback 服务：

```powershell
python .\scripts\serve_m96_pose_pilot.py --bundle .\data\annotations\m96-halpe26-development-pilot-v1 --role A --port 8766
python .\scripts\serve_m96_pose_pilot.py --bundle .\data\annotations\m96-halpe26-development-pilot-v1 --role B --port 8767
```

标注页支持 localStorage 草稿和导入 CSV 恢复。只有 A/B 各自完成并通过严格 intake 后，才允许从两份原始导出原子生成新的 C 裁决包；C 必须使用第三个角色 ID，只能看到必要分歧，不得看到模型候选：

```powershell
python .\scripts\build_m96_pose_pilot_adjudication.py `
  --pilot .\data\annotations\m96-halpe26-development-pilot-v1 `
  --annotator-a-csv <A.csv> `
  --annotator-b-csv <B.csv> `
  --output <new-C-bundle>
python .\scripts\serve_m96_pose_pilot.py --bundle <new-C-bundle> --role C --port 8768
```

intake 会保存 revision、原始字节 SHA256 并拒绝 A/B/C 角色 ID 复用。空白 pilot 的审计摘要、不可变合同与字段记录见
[`reports/m96-pose-pilot/summary.md`](../reports/m96-pose-pilot/summary.md)、
[`pilot-contract.json`](../reports/m96-pose-pilot/pilot-contract.json) 和
[`field-change-record.json`](../reports/m96-pose-pilot/field-change-record.json)。这三项记录的是可执行入口和 0 人工行现状，不得声称已经完成真人标注或得到准确率。

## 12. 部署和信任边界

- 当前是单节点 SQLite + 单常驻 Worker 架构，未实现水平扩展队列；Worker 默认使用 GPU RTMPose，
  CUDA 不可用时回退 CPU，计算池默认上限为 4 线程。
- Bearer 是可选部署门禁，不是用户/租户权限模型；生产应由 TLS 反向代理、网络隔离、速率/体积限制和审计补齐。
- 上传扩展名白名单不是内容信任；服务还会实际探测解码，但没有恶意媒体沙箱声明。
- `/v1/jobs` 和单作业 `summary` 可能暴露文件名、模型路径与运行元数据，非本机部署应强制鉴权。
- 普通用户 Demo 不展示 `job.error`、异常正文、raw JSON 或内部分析报告链接；这不改变 API 本身的管理与
  调试字段，因此非本机 API 仍需鉴权和最小权限代理。
- 主服务产物下载只允许 18 个精确名称；M93 私有 intake 不在这条 allowlist，也不应放入可公开目录。
- 生产标定权威只能来自部署配置，不允许客户端上传的 request JSON 覆盖。
- lifecycle registry、标定资产、晋级账本、运行 Profile 绑定和机位证据在正式评分路径中按路径、版本和 SHA 精确重放；缺失或漂移时关闭评分。
- 当前哈希用于内容身份和漂移检查，不等同于签名者身份或可信时间戳。生产仍需 OS ACL、不可变制品库和独立审批。
- 当前文档站必须用 `scripts/serve_current_docs.ps1` 启动；它只把
  `reports/rallymate-current-docs` 加入服务白名单。不要用通用仓库根目录 HTTP 服务代替，否则会扩大可读取范围。
- M96 pilot 的 loopback 服务是另一个固定白名单入口，只允许请求所选角色包和必要视频；预测、候选输出及仓库其他文件应返回 404。它不属于主服务 9 个 HTTP 路由，也不改变 18 项产物下载白名单。

## 13. 最小客户端示例

PowerShell 上传并轮询：

```powershell
$headers = @{ Authorization = "Bearer $env:RALLYMATE_API_KEY" }
$job = Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8000/v1/jobs' `
  -Headers $headers `
  -Form @{ video = Get-Item '.\FULL-TEST\sample.mp4' }

do {
  Start-Sleep -Seconds 2
  $state = Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000/v1/jobs/$($job.id)" `
    -Headers $headers
} while ($state.status -in @('queued', 'running'))

if ($state.status -eq 'succeeded') {
  Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000$($state.demo_result_url)" `
    -Headers $headers
}
```

若部署未配置 `RALLYMATE_API_KEY`，省略 `-Headers $headers`。示例中的文件名仅为占位，实际请选择存在的视频。

## 14. 源码定位

- HTTP 与 allowlist：`src/rallymate_service/api.py`
- SQLite 状态机：`src/rallymate_service/database.py`
- 常驻模型 Worker：`src/rallymate_service/worker.py`
- 用户结果适配：`src/rallymate_service/user_demo.py`
- 当前 Demo v1.2 结果与 Beta 评价：`src/rallymate_service/user_demo.py`、
  `src/rallymate_service/training_evaluation.py`
- Demo v1.2 实现摘要与精确合同：[`reports/user-demo-v1.2/summary.md`](../reports/user-demo-v1.2/summary.md)、
  [`demo-contract.json`](../reports/user-demo-v1.2/demo-contract.json)
- Demo v1.2 字段留存记录：[`reports/user-demo-v1.2/field-change-record.json`](../reports/user-demo-v1.2/field-change-record.json)
- Demo v1.1 历史实施摘要与精确合同：[`reports/m96-user-demo-v1.1/summary.md`](../reports/m96-user-demo-v1.1/summary.md)、[`demo-contract.json`](../reports/m96-user-demo-v1.1/demo-contract.json)
- Demo v1.1 历史字段记录：[`reports/m96-user-demo-v1.1/field-change-record.json`](../reports/m96-user-demo-v1.1/field-change-record.json)
- 推理与产物链：`src/rallymate_vision/pipeline.py`
- 298 项能力审计：`src/rallymate_scoring/granularity.py`
- M89 交接：`src/rallymate_annotation/scoring_truth_event_handoff.py`
- M90 授权：`src/rallymate_annotation/scoring_truth_authorization.py`
- M93 A/B/C 执行：`src/rallymate_annotation/scoring_truth_event_execution.py`
- M93 私有 intake：`src/rallymate_annotation/scoring_truth_event_phase_intake.py`
- Pose preset：`models/rtmpose/deployment-presets.json`
- 模型候选：`models/rtmpose/model-candidates.json`
- M96 M/L/X 同帧专项：[`docs/RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md`](RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md)、[`summary.md`](../reports/m96-rtmpose-same-frame-diagnostic/summary.md)、[`field-change-record.json`](../reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json)
- M96 evaluation-only pilot：[`reports/m96-pose-pilot/summary.md`](../reports/m96-pose-pilot/summary.md)、[`pilot-contract.json`](../reports/m96-pose-pilot/pilot-contract.json)、[`field-change-record.json`](../reports/m96-pose-pilot/field-change-record.json)
- M97 X 测量恢复：[`summary.md`](../reports/measurement-recovery-m97/summary/summary.md)、[`report.json`](../reports/measurement-recovery-m97/summary/report.json)、[`field-change-record.json`](../reports/measurement-recovery-m97/field-change-record.json)
