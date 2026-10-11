# 服务端、任务队列与报告装配逻辑

本文对应 2026-10-09 工作区提交 `70a744d`。源码引用的 `L数字` 是该快照的行号。此篇解释 `rallymate_service` 及部署入口；视觉、正式评分和网页实现见同目录其他分篇。

## 1. 服务层在整套系统中的位置

服务层解决四件事：接收并持久化输入视频；将 GPU 工作变成可查询的异步任务；运行并保存分析产物；把严格的算法数据转换为适合网页阅读的报告。它不在上传 HTTP 请求里同步完成整段视频推理，也不把数据库当作逐帧坐标的存储系统。

持久层是两种载体：SQLite 保存任务状态与摘要，文件系统保存视频、请求快照与 JSON/JSONL/MP4 产物。API 和 worker 通过同一个数据库及同一个数据目录协作，没有使用 Redis、Celery 或独立消息代理。

核心文件：`api.py` 负责 HTTP 边界，`config.py` 负责部署配置，`uploads.py` 负责分块上传，`database.py` 负责队列，`worker.py` 负责模型常驻与执行，`user_demo.py` 负责报告装配。

## 2. 启动与配置解析

### 2.1 三种服务启动方式

`pyproject.toml` 定义 `rallymate-api`、`rallymate-worker`、`rallymate-dev` 三个 console script，分别映射到 `cli.py`。

- `cli.api_main`（L14）启动 uvicorn，目标为 `rallymate_service.api:app`。默认监听 `0.0.0.0:8000`，默认一个 API worker。这里的 worker 数是 HTTP 进程数，不等于 GPU 推理 worker 数。
- `cli.worker_main`（L30）加载配置，建立 `JobDatabase`，进入 `run_worker`。支持 `--once`；轮询间隔最低 0.1 秒。
- `cli.dev_main`（L49）把 API 与一个后台推理线程放在单个进程中。先 `bind_socket()`，确认端口能占用，再启动模型线程。这样重复启动遇到端口冲突时，不会先加载另一份 GPU 模型。退出时发 stop event，最多 join 15 秒。

`api.py:L1374` 的 `app = create_app()` 是模块级构造。导入 API 模块会解析配置并创建所需目录；数据库与评审表的初始化在 FastAPI lifespan 中完成（L414）。因此“只 import 一个函数”也可能触发 API 启动配置检查。

### 2.2 配置默认值的两个层级

`ServiceSettings`（`config.py:L100`）是不可变 dataclass，其字面默认 `pose_backend="yolo"`。但正常启动用的是 `load_settings()`（L391），先解析 `RALLYMATE_POSE_PRESET`，未指定时调用注册表默认预设；实际默认是 RTMPose-M Halpe26 online。不要从 dataclass 的默认值推断正常部署使用 YOLO Pose。

预设能同时决定 model、backend、runtime、profile、config、native_keypoint_format。明确选择 `yolo-baseline` 才是回退方案。YOLO Detect 仍负责人物、球和球拍检测。

其他默认配置：

- 数据目录：仓库下 `service_data`；数据库 `service_data/jobs.sqlite3`。
- 视频上传限制：2 GiB；时长限制：30 分钟。
- 租约：3600 秒；最大领取次数：2。
- CPU 线程：4；GPU/CPU 选择：`device=auto`。
- 上传、请求、输出子目录分别为 `uploads`、`requests`、`runs`。

这些是代码默认，实际部署可以用环境变量覆盖；本说明没有读取或披露真实 `.env`、`.dev.vars`、`runtime.env` 值。

### 2.3 启动验证与注册表权限

`ServiceSettings.validate_license()` 不只检查 license 字段，还检查环境名称、姿态后端与拓扑、CPU 线程、可信校准资源、可写目录边界、CORS 与 public URL。

配置了 calibration assets，就必须同时指定可信晋升账本、runtime profile binding 注册表、runtime view evidence 目录。算法权威资源不能放在任务可写的 uploads/requests/runs 中。运行指标注册表必须由 `registry-lifecycle.json` 的 `runtime_feasibility` 角色授权；不能任意改一个路径就绕过当前指标定义。

`create_app` 会做大部分配置检查，但有意把 registry authority 的完整检查推迟给 readiness，使错误的注册表配置可以在 `/health/ready` 中得到诊断。worker 仍会严格验证后才工作。

## 3. 上传的完整事务边界

### 3.1 分块是传输单位

浏览器把一个视频切成字节块，不是切成多个可独立推理的视频片段。服务端最终合并回完整文件，再生成一个 job。

`uploads.py:L16` 的 `CHUNK_BYTES=4 MiB` 是最大接收上限与旧协议兼容值；L18 的 `DEFAULT_CHUNK_BYTES=128 KiB` 才是新上传会话的默认块大小。`/v1/meta` 仍公开 4 MiB 的 `chunk_bytes`，客户端必须以实际 create/status 返回的 `chunk_size` 为准。

一个会话目录位于 `uploads/.chunks/<upload_id>/`，包括：

- `manifest.json`：文件名、大小、选项、chunk_size、已接收块及 SHA-256、完成后的 job_id。
- `<index>.part`：已完成持久化的块。
- 临时 `.part.tmp` / `manifest.tmp`：写完、flush、fsync 后原子替换。
- `.lock`：跨进程文件锁；Windows 用 msvcrt，Unix 用 flock。

### 3.2 创建会话

`POST /v1/uploads` → `api.create_upload`（L983）→ `UploadStore.create`（`uploads.py:L89`）。

API 先校验扩展名、可接受的分析选项及严格类型，拒绝布尔值冒充整数、未知选项、非法时间窗和人数。store 再校验 UUID、字节数、磁盘空间和活跃会话数。

会话最多 64 个活跃项；磁盘检查保留约 `3 × (新文件大小 + 既有未完成会话预留大小) + 512 MiB` 的空间，因为块、组装文件和持久输入可能短暂共存。这里是保守空间预留，不是磁盘配额系统。

同 UUID 重试且文件名、大小、选项一致，返回现有会话；不一致返回 409。旧空会话若还采用大块协议会返回 410，促使客户端建新会话，而不是原地改协议影响飞行中的 PUT。

### 3.3 接收块

`PUT /v1/uploads/{upload_id}/chunks/{index}` → `api.upload_chunk`（L1019）→ 线程池内 `UploadStore.put`（L130）。

API 按流读取请求，实际字节超过 4 MiB 就拒绝，不信任 Content-Length。store 校验 `X-Chunk-SHA256`、索引范围、本块期望长度，再进入会话锁。

已有块且 digest 相同：幂等返回；同索引内容不同：409；新块落盘后更新 manifest。最后一块可短于标准块，其期望长度由完整视频大小计算。

### 3.4 合并与入队

`POST /v1/uploads/{id}/complete` → `UploadStore.complete`（L160）。

锁内先查询 `existing_job(upload_id)`。如果数据库已经有任务，就回填 manifest 并返回该任务，覆盖“已经入队但 HTTP 响应丢失”或“入队后进程崩溃”的重试情形。

尚未入队时要求所有块存在；按序再次验证每块大小和 digest，写 `assembled.video`，核对总长度。损坏块会从 manifest 删除，让客户端补传。enqueue 回调把组装文件包装为 UploadFile，调用 `_submit_video`，并固定 `job_id=upload_id`。成功后删除分块；组装临时文件在 finally 清理。

### 3.5 单次上传与分块上传汇合点

传统 `POST /v1/jobs`（L959）接受 multipart 视频；分块 complete 也最终调用 `_submit_video`（L780），因此两者共享视频检查与请求生成逻辑。

`_submit_video` 按下面顺序工作：

1. 校验 court mode、人数 1–4、开始/结束时间和 max_frames。
2. 解析受生命周期清单授权的指标注册表。
3. 读取并钉住 technique registry 的版本与 SHA-256，防止任务排队时规则变更后静默重新解释。
4. 清理文件名，仅接受 mp4/mov/m4v/avi/mkv。
5. 按 1 MiB 读取上传内容，保存 `uploads/<job_id>.<suffix>`，检查大小。
6. `probe_video` 验证可解码性、取得宽高/FPS/帧数/时长，检查 30 分钟限制。
7. 生成 `requests/<job_id>.json`；里面指定模型、推理参数、输出目录、scoring 资源和提交时 provenance。
8. `db.create_job()` 插入 queued 任务，返回 202。

请求中默认 detector size 960、pose size 640、检测阈值 0.15、姿态阈值 0.25、frame_stride=1、max_players=2、write_annotated_video=True。这些是 API 提交的配置，不是所有离线 CLI 请求的全局默认。

异常时，若数据库尚未创建任务，删除已保存的视频；已入队的任务不会因响应失败被当作未提交输入清理。视频、请求文件和 SQLite 插入不是一个跨介质 ACID 事务，幂等 UUID 和清理逻辑承担恢复责任。

## 4. SQLite 队列状态机

`database.py:L33` 创建 jobs 表，CHECK 约束只允许 `queued/running/succeeded/failed`。没有 cancelled 状态，也没有服务端取消端点。

```text
queued ──领取──> running ──正常完成──> succeeded
                   │
                   ├──执行异常────────> failed
                   │
                   └──租约过期──> queued（仍有领取预算）
                                或 failed（预算耗尽）
```

`JobDatabase.connect()`（L23）每次打开连接，启用 WAL，timeout=30 秒。队列状态带 created/updated/started/completed 时间、worker_id、attempts、lease_expires_at、progress_json、summary_json、error。时间使用 UTC ISO 文本。

`claim_next()`（L132）在 `BEGIN IMMEDIATE` 事务中：先把过期 running 任务按 attempts 分别重新排队或标 failed；再按 created_at 取最老的 queued；写入 running、worker、租约并 attempts+1。started_at 用 COALESCE 保留第一次启动时间。

重要区别：**普通推理异常直接 failed；自动重领针对的是租约过期任务。** 不能把 max_attempts=2 理解为任何异常都自动重跑两次。

`update_progress()`（L204）写进度并刷新租约；`mark_succeeded()`（L228）保存 summary，并置 completed/100%；`mark_failed()`（L262）保存截断后的异常文本，清掉租约。

这套实现注明适合单 GPU 节点和单 worker。领取事务能避免同时拿走同一 queued job，但完成/进度 SQL 的条件是 job_id 与 running 状态，没有独立的租约 generation token。这是理解多 worker 扩展边界时需要注意的实现事实。

## 5. 常驻推理 worker

`PersistentVisionRunner.__init__`（`worker.py:L132`）在模型加载前设置 Torch intra-op/inter-op 与 OpenCV 线程数，设置后再次读取验证。如果线程数无法生效，启动失败，而不是带着不可控线程池继续运行。

随后只加载一次 `Yolo26Perception`，并保存在 runner 中。类名保留 YOLO 历史名称，但 pose 可以由 RTMPose 后端承担。多次任务复用模型对象，避免每上传一次视频重新加载网络。

`run_worker()`（L259）建立数据库、生成 worker_id、加载 runner，然后串行循环调用 `process_one()`。无任务时等待 poll_seconds；有 stop_event 时可以结束空闲等待。`process_one()`（L197）执行链为：

```text
检查配置和模型文件
→ claim_next
→ load_request(request_path)
→ 构造 progress_callback（同时续租）
→ runner(request, trusted resources...)
→ run_pipeline
→ mark_succeeded(summary)
```

runner 接口用 inspect.signature 判断是否支持各关键字，便于测试用轻量 runner 替代真实模型。带 calibration assets 的任务，其 runtime view evidence 文件根据原视频 SHA-256 命名匹配，不能由客户端任意提供一个视角声明就通过可信绑定。

`/health/ready` 验证配置、模型文件、数据库和校准资源，不直接证明 GPU worker 已加载成功或队列正在消费。API ready、worker ready、真实任务完成是不同检查层次。

## 6. HTTP 接口地图

以下是当前 `api.py` 的实际路由。所有 `/v1` 接口均经过 authorize；若配置了 key，`/health/ready` 也需要 Bearer。`/health/live` 仅返回存活状态。

- `GET /health/live`（L486）：进程存活。
- `GET /health/ready`（L553）：模型文件、注册表/校准/配置、数据库与队列状态。
- `GET /v1/meta`（L490）：API 版本、认证模式、上传和报告能力。
- `GET /v1/techniques`（L538）：24 项技术目录及证据要求。
- `GET /v1/model-capabilities`（L672）：模型/设备/能力信息。
- `GET /v1/jobs`（L755）：最近任务，limit 默认 20，上限 100。
- `POST /v1/jobs`（L959）：传统单次上传并入队。
- `POST /v1/uploads`、`GET /v1/uploads/{id}`、`PUT .../chunks/{index}`、`POST .../complete`：分块协议。
- `GET /v1/jobs/{id}`（L1043）：任务状态、进度、摘要与产物地址。
- `GET .../demo-result`（L1098）：用户报告，要求 succeeded。
- `GET .../trajectory`（L1160）：读取帧产物构建球路；running/succeeded 都可用。
- `GET .../pose-preview`（L1236）：读取最多 10 秒、最多 600 样本的骨架窗口。
- `GET .../technique-assessment`（L1279）：完成任务的定性技术证据评估。
- `GET/POST .../technical-review`、`GET .../technical-review/export`（L1063）：保留的人工评审持久接口，当前网页已移除人工评级提交表单；只读原文核验与来源对应测量仍展示。
- `GET .../artifacts/{name}`（L1330）：下载白名单产物，要求 succeeded。
- `GET /`（L662）：API 服务介绍。

API_VERSION 为 1.3.0，而 OpenAPI 兼容版本保持 1.2.0（L77–78）。这不是两套服务；是当前能力发现与旧客户端兼容的版本表达不同。

## 7. 身份、错误和数据边界

`authorize()`（L458）用 `secrets.compare_digest` 校验 Bearer。没有 key 的纯本地开发可以访问；公开或 production 场景缺 key 时，敏感路径返回 503，不因为 readiness 已经提示错误就继续匿名开放任务。网页同源网关负责服务端注入内部 key，浏览器不需要持有这个 key。

这是一把部署级 API key，不能自动视为用户账号权限系统。jobs 表没有 user_id，API 最近任务查询也没有用户归属过滤。是否允许外部访问哪些路径，还需要结合网页网关的白名单理解。

request_id middleware 会生成/清理 request ID，写入响应头及请求 provenance。公开摘要与错误会移除 worker 本地路径等内部信息。CORS 使用明确来源，不启用 cookie credentials；公开部署不允许 `*`。

artifacts 采用文件名白名单，不接受任意相对路径。JSON/视频返回 `private, no-store`；动态轨迹、骨架与人工评审也禁用缓存。`annotated.mp4`、两个 HTML 报告可 inline 预览，其他产物 attachment 下载。

## 8. 原始产物如何变成报告

`GET demo-result` 是报告装配入口，不再次执行神经网络。

```text
数据库 summary
  + indicator-features.jsonl
  + events.jsonl
  + source-aligned-measurements.json
  + 必要时从 frames/primary-player 更新动作候选
→ build_user_demo_result
→ footwork_review
→ source_aligned_assessment
→ build_analysis_report
→ project_report(current/legacy-v1)
```

### 8.1 动作候选的懒更新

`summary_with_actions()`（`api.py:L390`）比较 detector_version 与 analysis_version。成功的历史任务如果缺当前版本，就从 frames/primary-player 重新识别动作候选，最多缓存 12 个签名结果。返回时补到复制的 summary，不覆写原始 summary.json，也不重新跑 detector/pose。

### 8.2 空测量是合法完成结果

`demo-result` 允许 indicator-features 文件缺失或为空时 records=[]，生成“未观测/证据不足”的报告，避免强迫重传或制造分数。非空文件仍经过严格读取，拒绝重复 JSON 键和非有限数字。job 成功仅表示执行链成功，不表示一定识别到有效动作。

### 8.3 稳定的 13 项指标

`user_demo.py:L36` 的三组映射为：FS01-M02/M03/M04/M05；FS02-M02/M03/M04/M05；FS09-M01/M02/M03/M04/M05。共 13 项，分别属于准备与分腿垫步、第一步启动、制动与重新稳定。

24 项技术目录、298 张来源指标卡、246 条视觉要求和这 13 项自动测量指标是不同维度，不能互换计数。

### 8.4 当前坐标和时间提醒

`build_user_demo_result()`（L381）检查 coordinate contract 是否为 `isotropic-frame-long-edge-v1.1.0`。旧合同保留数值以供复核，但标记需要重新分析；还根据 camera motion 与 source timing 提示不能用于固定机位位移/速度的区间。

## 9. 参考分的精确公式

### 9.1 主参考分的含义

`training_evaluation.py:L15–34` 将 score semantics 固定为 `measurement_evidence_quality`，`technical_score_0_to_100`、`technical_grade` 始终为空，technical status 为 calibration_required。

对一个指标，五个分量都按百分数表示：

```text
M = 可测实例数 / 该指标去重与身份校验后保留的实例总数 × 100
C = 可测实例中的有效必需特征槽数 / (保留实例总数 × 必需特征数) × 100
Q = 可测实例中有效必需特征的有限置信度中位数 × 100
R = 可计算的各特征跨独立事件重复性的中位数
E = 通过评分证据门控且必需特征齐全的实例数 / 实例总数 × 100

基础权重：M 0.30，C 0.20，Q 0.15，R 0.25，E 0.10
指标参考分 = round(Σ 已存在分量 × 归一化后的有效权重)
```

“可测实例”由 `_is_measured()`（L292）判定：优先 `feature_status`、缺失时才看 `scoring_feature_status`，值必须为 measured，且质量门没有明确 `measurement_allowed=false`。C/Q/R/E 的数据收集仅发生在这些可测实例内；分母中的保留实例仍包含不可测实例。M/C/E 通过 `_percent()` 先取整并限幅 0–100，Q 取整并限幅，R 取整，然后进入加权。[training_evaluation.py:469](/C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:469)

`_reference_score()`（L37）不会给缺失 Q/R 填 0 或 50；删掉缺失分量的权重后重新归一化。只要没有可测记录，或没有有效必需特征槽，score=null；两者都存在才可计算。最终结果再次限幅到 0–100。

示例仅用于展示公式：假设 M=100、C=80、Q=90、R 缺失、E=0，那么分母为 0.75，结果为 round((30+16+13.5)/0.75)=79。这个 79 表示测量证据情况，不意味着球员动作技术达到 79 分；E=0 也说明参考分存在不等于正式评分可用。

### 9.2 重复性并非技术正确性

`_repeatability_for_feature()`（L360）：

- 少于两个独立样本：返回 None。
- `_code` 分类值：众数频率/样本数。
- 方向角：计算平均 sin/cos 的合向量长度，避免 +179° 和 -179° 被当成相反方向。
- 其他连续值：按 inclusive 四分位求 Q1、Q3，`scale=max(abs(median),abs(Q1),abs(Q3),0.05)`，`dispersion=min(1,abs(Q3-Q1)/(2*scale))`，重复性为 `(1-dispersion)*100`。

一个错误动作重复得很一致，也可能有很高的重复性。因此代码只用于证据重复性，不生成“技术优秀”判断。

### 9.3 独立样本与去重

`validated_training_records()`（L609）先按 `(video_id, person_track_id, event_id, indicator_id)` 分组。完全相同的重复行只算一次；同一身份有冲突内容则整组拒绝；event_code 与指标家族不匹配则剔除。缺身份的历史记录，每指标最多保留一个无身份单例，不能建立跨事件重复性。

这里“独立”是程序对不同完整事件身份的计数约定；该函数不检查不同 event_id 的时间窗是否重叠，也不证明统计独立性，不能将此样本数直接解释为实验独立重复数。[training_evaluation.py:302](/C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:302)、[505](/C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:505)

有效特征要求 `valid is True`、value 为有限数，原始 measurement features 优先，再添加仅存在于 scoring context 中的名字。相同名字只取第一项；不能用后出现的评分字段覆盖测量值。

### 9.4 总分怎么聚合

`build_training_evaluation()`（L641）先形成全部 13 项评价，再对有可用分数的指标做**不加权平均**。每个 FS 动作家族也对本家族可用指标做不加权平均。不是先平均三个动作再平均，也不是按事件数加权。没有测量的指标不按 0 分加入均值，同时单独公开已测/总指标数。

### 9.5 其他兼容分数

`user_demo.py` 还保留两种信息摘要，不能误当主技术总分：

1. 动作信息成型分 `_formation_assessment`（L324）：候选轮廓 30% + 已测指标比例 30% + 测量覆盖 20% + 幅度种类覆盖 20%。`final_demo_score` 是有信息成型分的动作之间的均值。
2. 分析完成度（L535）：观察到的动作家族比例 30% + 已测唯一指标比例 45% + 三组动作平均测量覆盖 25%。`display_score` 是它的兼容别名。

`formal_scoring`（L657）在用户报告中固定 `available=false`、`status=calibration_required`、`score_0_to_100=null`、`grade=null`。即使离线原始 summary 带入一个 grade，这个报告构造器也不会把它自动公布为正式教练分。

`score_explanation.py` 把分量、有效权重、实际贡献、排除原因、样本数和证据缺口转成可解释对象。总分解释使用与聚合相同的指标集合，避免页面自行猜权重。

## 10. 逐项测量与训练建议

`footwork_review.build_footwork_review()`（L253）将事件与指标绑定，连接键同时检查 video_id、event_id、event_code、person_track_id。冲突重复事件和跨人物记录被拒绝。每个指标可以有整体 feature_status 与单项 measurements 两层状态：整体不能评分时，独立有效的单项测量仍可能保留。

响应同时有总 episode_count、returned_episode_count、is_truncated。`footwork_review.measurement_summary` 的 scope 明确涵盖全部已绑定事件，包括超出回放列表上限的事件；网页不能把返回列表长度误当整段视频总数。另一个同名对象 `analysis_report.measurement_summary` 则从实际返回的回放 episodes 重新统计，scope 为 `returned_replay_episodes` 并保留 is_truncated，两者不可混用。[footwork_review.py:342](/C:/Users/Admin/Documents/网球/src/rallymate_service/footwork_review.py:342)、[analysis_report.py:251](/C:/Users/Admin/Documents/网球/src/rallymate_service/analysis_report.py:251)

`source_assessment.py` 对 source-aligned-measurements 再做来源、人物、事件、时间、视频与 frames digest 绑定，生成独立来源测量面板，不覆盖历史特征或旧参考分。

`analysis_report.build_analysis_report()`（L147）按真实证据选最多三项回看重点：优先有部分有效测量的步伐片段；有连续二维肩髋轴观测则提示对应转体区间；动作分类不确定则建议确认动作类型；没有有效候选就建议改善采集。

报告 layers 按 video/time、subject、recognition、measurement、evaluation 分层，各自记录状态。肩髋角变化只表示二维投影，不直接判定三维转体、动力链或技术错误。这里的建议是确定性证据摘要，不依赖大语言模型。

## 11. 轨迹、骨架缓存与报告兼容

轨迹 API 从 frames.jsonl 动态构建，缓存键包括文件路径、mtime_ns、文件大小、job 状态、sample_limit、prediction_horizon。最多缓存 6 个结果。running 时允许读取尚在写入的帧文件；完成后缓存键变化，强制用最终状态构建。

pose-preview 类似，但额外绑定 primary-player.jsonl 和时间窗，最多 16 个缓存项。它最多读 10 秒、600 样本，避免一次把整段骨架塞入浏览器。

球预测 horizon 默认 400ms，上限 5000ms；API 描述明确为 constant-velocity heuristic，不是物理仿真。球拍只是 bbox 可观测性，不是球拍关键点、拍面朝向或触球检测。

`report_compatibility.py:L16` 的默认 JSON 报告为 `legacy-v1`；当前网页主动请求 current。投影只复制已存在字段，不修改缓存原对象，不把局部可测提升成整体可评分；新版不支持的模型分类不会伪装成旧 rule_inferred。原始 artifact 下载默认 current 原字节，只有显式请求 legacy-v1 的 summary 下载才构造兼容副本。

## 12. 当前隐藏的人工评审后端

`technical_review.py` 保存于独立 `technical-reviews.sqlite3`。当前分支移除的是网页人工评级提交表单，接口、客户端方法、模型和历史数据逻辑仍存在。网页仍在 `scoring-demo-web/app/LiveResults.tsx:L120` 渲染 `SourceMeasurementPanel` 的只读原文对应测量，并在折叠区域渲染 `RuleReviewPanel` 原文核验；不能把表单移除理解成原文规则与测量页面也被移除。

输入包含来源视频 hash、原文规则版本/hash、事件/人物产物上下文 hash、reviewer 自报身份、人物/事件/时间窗、可观测性、等级/状态、原因与下一步。Pydantic strict + extra=forbid，拒绝额外字段、类型强转、非有限 JSON 和重复键。

保存前核对所有来源指纹与时间窗；用 expected_revision 防止覆盖别人更新，用 mutation_id+payload digest 保证完全相同请求的幂等。修订不能更换原来源、人物、事件、时段、规则、评审人；修改意见生成新的 revision。

数据库只有 append-only revisions 表，触发器拒绝 UPDATE 和 DELETE。多个评审人分别保存，不自动多数投票。记录的 hash 链是内容可追踪机制，不是认证签名或可信时间戳。

298 张指标卡可以在观察充分、来源规则无阻塞等条件下记录人工 A–E；视觉规则记录 observed/not_observed/unassessable。partial 或 unobservable 不会被自动判为 E。导出提供 coach_label_candidates，但 calibration_eligible=False，仍需独立裁定、真值核验与按来源分离的数据划分。

## 13. 部署与运维代码

`deploy/Dockerfile` 使用 CUDA runtime Ubuntu22.04、Python3.10、ffmpeg，安装 service/training/rtmpose 依赖，运行时切到 uid10001。`docker-compose.yml` 把 API 和 GPU worker 拆成两个服务，共享 service_data，models/calibration 只读挂载；GPU 分配在 worker 上，API 默认仅向宿主 loopback 暴露端口。

`deploy/autodl/supervisord.conf` 管理 api、worker、web、cloudflared、monitor 五个进程。`run-process.sh` 从受保护 runtime.env 载入服务端配置，限制 CPU 线程、检查 key 长度，分别启动入口。Cloudflare tunnel 指向网页入口，网页再代理 API。

`monitor.py` 采集进程、HTTP、GPU、磁盘、队列和上传会话状态；`check_remote.py` 对监控快照做安全压缩及代码版本状态比较。它们是运维工具，不参与算法评分。没有在本次分析中运行远程部署或修改正在工作的服务。

Windows 的 `start_local.ps1` 会验证端口与内部 key、设置同源代理，然后运行完整服务。`restart_preview_service.ps1` 对端口归属和队列做检查，有 running/queued 或健康无法确认时拒绝停止服务；这是维护时的检查，不是数据库层的入队 drain 锁。不同启动脚本默认端口不完全相同，阅读命令时以参数和当前脚本为准。

## 14. 测试如何保护服务语义

建议对照以下现有测试阅读边界：

- `tests/test_service.py`：上传→队列→替身 worker→查询结果的 HTTP 闭环，以及空指标结果、注册表授权。
- `tests/test_service_boundaries.py`：CORS、public URL、认证、路径脱敏、可信路径限制。
- `tests/test_training_evaluation.py`：13 项评价、缺失不补分、事件去重、单样本无重复性、角度环绕。
- `tests/test_user_demo.py`：参考分分离、无效输入不能抬高分数、summary 中等级不能透传为正式分。
- `tests/test_report_compatibility.py`：兼容投影不能改变测量事实、不能伪造旧分类。
- `tests/test_technical_review.py`：幂等、修订冲突、来源绑定、多个评审人、append-only 历史。

这些测试多数验证规则和数据合同，不等于模型在真实比赛中的识别准确率。当前说明主要依据静态源码与测试意图；实际执行的测试范围在主文档记录。
