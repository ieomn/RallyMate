# 前端完整代码逻辑：scoring-demo-web

本文依据当前工作区实际实现及测试阅读整理，时间为 2026-10-09。没有启动真实分析、调用外部模型或读取密钥文件。源码定位采用“文件 + 精确起始行”；目录均位于 `C:/Users/Admin/Documents/网球/scoring-demo-web`。测试章节说明测试写了什么，不把阅读测试等同于本次已经执行通过。

## 1. 先建立正确的整体认识

当前前端是一个网球训练视频复盘工作台：接收视频，向 Python 分析服务提交持久化分块上传，观察后台任务，读回动作/测量/球路/骨架数据，提供回放、原文核验和报告导出。浏览器本身不运行人体、球或球拍识别模型。核心入口是 `ScoreLab`，不是文件名暗示的“前端现场打分实验”。

有四件事必须区分：

1. **网站框架**：保留 Next App Router 文件组织和 `next/*` 导入，但实际 `dev/build/start` 命令运行 **vinext**，底层使用 Vite 和 Cloudflare 插件；`next.config.ts` 是空配置，没有 Next rewrites。[package.json:8](C:/Users/Admin/Documents/网球/scoring-demo-web/package.json:8)、[vite.config.ts:73](C:/Users/Admin/Documents/网球/scoring-demo-web/vite.config.ts:73)、[next.config.ts:3](C:/Users/Admin/Documents/网球/scoring-demo-web/next.config.ts:3)
2. **真实报告**：从 `/v1/jobs/{id}/demo-result` 读取。名字含 `demo` 是历史 API 命名，当前页面将其作为用户任务真实结果使用，并校验任务 ID 与 `status=ready`。[analysis-session.ts:79](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-session.ts:79)
3. **证据参考分**：只有明确声明 `score_semantics=measurement_evidence_quality` 的分数可以展示；所有技术分、技术等级和正式等级会被归一化层清空。它回答“本次测量证据有多完整”，不是“球员技术有多好”。[measurement-evidence.ts:10](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/measurement-evidence.ts:10)、[measurement-evidence.ts:53](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/measurement-evidence.ts:53)
4. **旧评分演示**：`app/scoring/engine.ts`、`scenarios.ts` 仍保留确定性合成评分；当前 `ScoreLab` 没有导入或调用这两者。旧 `/api/scorecard` 的 POST 已返回 410，GET 改为动作目录。不要把旧引擎的 70%/30% 权重解释成当前真实报告的评分公式。[ScoreLab.tsx:3](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:3)、[scorecard/route.ts:5](C:/Users/Admin/Documents/网球/scoring-demo-web/app/api/scorecard/route.ts:5)

## 2. 页面、组件和数据流总图

```mermaid
flowchart TD
  Root[访问 /] --> Gate{有 view 或 job 参数?}
  Gate -->|无| Welcome[/welcome 宣传页]
  Gate -->|有| Lab[ScoreLab：全局业务编排]
  Welcome -->|原生链接| Lab
  Lab --> Shell[ProductShell：桌面/移动导航]
  Shell --> Home[TrainingHome]
  Shell --> Sessions[SessionLibrary]
  Shell --> Guide[CaptureGuide]
  Shell --> Analysis[视频分析视图]
  Analysis --> Upload[uploadResumable]
  Upload --> API[API client]
  API --> Gateway[Vite dev proxy / Worker gateway]
  Gateway --> Python[Python /v1 分析服务]
  Lab --> Watch[watchAnalysis 轮询任务]
  Watch --> Result[主结果 + 可选轨迹 + 可选技术评价]
  Result --> Normalize[normalizeMeasurementResult]
  Normalize --> Live[LiveResults]
  Live --> Replay[视频 + 球轨迹 + 按窗骨架]
  Live --> Panels[概览 / 原文测量 / 分数解释 / 时间线 / 详情]
  Lab --> Advice[/api/advice 服务端取证据并调用 MiMo]
  Lab --> Export[本地 MD / HTML / JSON 导出]
```

### 2.1 入口与宣传页

- `app/page.tsx` 是服务器页面。读取 `searchParams`，没有 `view` 且没有 `job` 就重定向 `/welcome`；有任一参数就渲染 `ScoreLab`。传入的仅为静态指标目录的版本和来源统计，不是预计算成绩。[page.tsx:5](C:/Users/Admin/Documents/网球/scoring-demo-web/app/page.tsx:5)
- `app/layout.tsx` 统一导入工作台、报告、分析、动作、规则以及宣传页样式；设中文页面语言和 RallyMate 元信息。[layout.tsx:1](C:/Users/Admin/Documents/网球/scoring-demo-web/app/layout.tsx:1)
- `/welcome` 只渲染 `MarketingLanding`。宣传页通过普通 `<a href="/?view=...">` 进入工作台，源码明确说明生产构建使用完整页面导航，不依赖 vinext beta 客户端导航。[welcome/page.tsx:9](C:/Users/Admin/Documents/网球/scoring-demo-web/app/welcome/page.tsx:9)、[MarketingLanding.tsx:1](C:/Users/Admin/Documents/网球/scoring-demo-web/app/welcome/MarketingLanding.tsx:1)
- `MarketingLanding` 的状态只是移动菜单、当前滚动区块和三步引导；副作用包括滚动进度、IntersectionObserver 入场动画、Esc 收起菜单、鼠标视差、减少动画偏好。卸载时清理监听和动画。[MarketingLanding.tsx:22](C:/Users/Admin/Documents/网球/scoring-demo-web/app/welcome/MarketingLanding.tsx:22)
- `TrainingExperience` 是宣传交互示意：二次 Bézier 曲线计算球的位置，进度分为准备/挥拍/恢复三段；播放用 requestAnimationFrame 推动 0～100%，离开视口、隐藏标签页或启用减少动画时停止。它没有 API 请求，明确标注“非真实视频测量”。[TrainingExperience.tsx:5](C:/Users/Admin/Documents/网球/scoring-demo-web/app/welcome/TrainingExperience.tsx:5)、[TrainingExperience.tsx:45](C:/Users/Admin/Documents/网球/scoring-demo-web/app/welcome/TrainingExperience.tsx:45)、[TrainingExperience.tsx:144](C:/Users/Admin/Documents/网球/scoring-demo-web/app/welcome/TrainingExperience.tsx:144)

### 2.2 ProductWorkspace 是呈现层，ScoreLab 是业务控制层

`ProductWorkspace.tsx` 导出四类视图组件，没有任务提交或任务轮询：

| 导出/内部组件 | 职责 | 精确源码入口 |
|---|---|---|
| `ProductIcon` | 内联 SVG 图标字典，统一尺寸和线宽 | [ProductWorkspace.tsx:12](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ProductWorkspace.tsx:12) |
| `WorkspaceSurface` | 主内容容器和视图切换动画；自身不以 view 为 key 重建 | [ProductWorkspace.tsx:46](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ProductWorkspace.tsx:46) |
| `ProductShell` | 侧栏、面包屑、返回首页、新建按钮、移动底栏、busy 指示 | [ProductWorkspace.tsx:64](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ProductWorkspace.tsx:64) |
| `TrainingHome` | 最近 3 份报告、空状态、分析中提示、拍摄/复盘引导 | [ProductWorkspace.tsx:121](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ProductWorkspace.tsx:121) |
| `SessionLibrary` | 本浏览器记录列表，名称搜索，按最近查看时间升降排序 | [ProductWorkspace.tsx:134](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ProductWorkspace.tsx:134) |
| `CaptureGuide` | 四类拍摄指南和可选检查清单；勾选不参与上传校验 | [ProductWorkspace.tsx:143](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ProductWorkspace.tsx:143) |
| `ReportPreview` | 从同源任务 artifact 读取缩略图；失败保留球场 SVG | [ProductWorkspace.tsx:103](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ProductWorkspace.tsx:103) |

`ScoreLab` 持有任务、文件、导航、报告、历史和建议状态，并把数据/回调传给这些组件。切换工作台视图不会卸载 `ScoreLab`，因此轮询继续；但分析视图是条件渲染的，离开 analysis 后其下的 `LiveResults`/播放器会卸载，返回后播放器局部状态会重新建立。不要将容器“不用 key 重建”的注释理解成跨四个视图永远保留播放器实例。[ScoreLab.tsx:353](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:353)

## 3. ScoreLab 的状态模型和生命周期

### 3.1 三个互相独立的状态轴

| 状态 | 取值 | 作用 |
|---|---|---|
| `view` | overview / sessions / analysis / guide | 决定当前导航视图 |
| `uploadState` | idle / ready / uploading / processing / complete / error | 决定上传、进度、恢复和导出控件 |
| `scoreContext` | demo / live-pending / live | 决定是否已有报告上下文；`hasReport = scoreContext !== demo` |
| `importState` | idle / reading / success / error | 导入 JSON 文件的独立生命周期 |
| `job` | 后端 JobProgress 或 null | 保存 queued/running/succeeded/failed 等真实服务状态 |
| `evidence` | mode、jobId、trajectory、assessment、result、error | 当前报告的数据聚合容器 |

具体声明集中于 [ScoreLab.tsx:100](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:100)。这里 `demo` 主要表示“尚未进入真实报告上下文”，并不意味着 UI 当前正在生成演示分。

另外保留 `videoFile`、`localVideoSrc`、`localVideoJobId`，确保本地原片只配对相同任务；`uploadProgress` 保存服务器确认收到的字节进度；`history` 保存最多 12 条最近报告；`savedAdvice` 按任务和主题隔离。

### 3.2 防止异步结果串到其他任务

- `analysisRunRef` 是运行世代计数器。新分析、重新选视频、导入报告都会递增；旧上传或旧导入读完后，必须确认 runId 仍一致才可写状态。
- `analysisAbort` 保存当前 AbortController；新 monitor 会 abort 旧 monitor；卸载会 abort 当前观察。
- `activeServiceJob` 记录当前绑定的后台任务，不以视图切换清空。
- `invalidateAnalysisRun` 还清空 importedSummary、activeServiceJob 和 localStorage 的 activeJob 标记。
- 本地 object URL 在换文件或卸载时 revoke，避免旧 Blob 常驻内存。

对应入口：[ScoreLab.tsx:150](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:150)、[ScoreLab.tsx:167](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:167)、[ScoreLab.tsx:222](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:222)。

### 3.3 URL 才是恢复导航的依据

`workspace-navigation.ts` 白名单四种 view；job ID 必须是 8～80 位字母、数字、下划线或连字符。显式合法 `view` 优先于 `job`：`?view=overview&job=...` 仍停留 overview。只有 analysis 链接会携带 job。[workspace-navigation.ts:15](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/workspace-navigation.ts:15)

`navigate` 使用 `history.pushState`，设置 React view 并滚到顶部。初始化和浏览器前进/后退通过 `popstate` 重新读取 URL：若 URL 为 analysis 且有不同 job，或旧 observer 已 abort，就清空本地文件并调用 `monitorJob`。[ScoreLab.tsx:143](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:143)、[ScoreLab.tsx:193](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:193)

当前代码会写 `rallymate.activeJob`，但恢复函数没有读取它；真正恢复依靠 URL job 深链或用户点击历史记录。刷新 overview 不会被旧 activeJob 强行带回分析。`rallymate.recentReports` 是本浏览器记录，不是服务器用户训练库：去重、校验 ID/日期、最多 12 条、名称最多 160 字。其时间是“最近查看”，不是拍摄时间。[training-report.ts:73](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/training-report.ts:73)

`newAnalysis` 如果 busy 就只跳回当前分析，不新建任务；非 busy 才清空现有状态。`acceptVideoFile` 校验文件名扩展名 MP4/MOV/M4V/AVI/MKV，而不是仅信 MIME。页面“建议 200 MB 以内”是建议文案，前端此处没有 200 MB 硬性限制。[ScoreLab.tsx:209](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:209)、[ScoreLab.tsx:363](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:363)

## 4. 从点击“开始分析”到报告的完整调用链

1. `acceptVideoFile(file)` 终止旧观察，创建本地 object URL，进入 `ready`。
2. `submitVideo()` 再次失效旧 run，创建 AbortController；进入 `uploading` + `live-pending`，清空旧结果。
3. `apiClient.submitVideo(file,{courtMode:'auto',writeAnnotatedVideo:true,...})` 转给 `uploadResumable`。
4. `uploadResumable` 计算上传会话指纹，POST `/v1/uploads` 创建/恢复会话，核验已存分块哈希；两个 worker PUT 缺失分块；POST `/complete` 完成合并。
5. 得到 JobSubmission 后校验 id，将本地视频与 jobId 绑定，立即加入本地历史；只有用户仍处于 analysis 视图时才 `replaceState` 写 job 深链。
6. `monitorJob(id)` 将 UI 置 processing、job 置 queued，写 activeJob，再调用 `watchAnalysis`。
7. `watchAnalysis` 每 2 秒 GET job。运行中最多每 15 秒异步补拉一次轻量轨迹预览，预览不阻塞轮询。
8. job 成功后并行读取主结果 `/demo-result`、轨迹 `/trajectory`、技术评价 `/technique-assessment`。
9. 主结果成功且 ID/status 匹配后，保存完整 evidence，`scoreContext=live`、`uploadState=complete`，更新最近查看时间。
10. `LiveResults` 再次归一化真实测量，派生训练重点、时间线、评分解释和原文核验；`ReplayPanel` 选择服务端视频或本地原片。
11. 用户点某一片段，派发带 jobId 的 `rallymate:seek-video`；播放器验证任务并跳到源时间对应位置。
12. 用户导出时，在浏览器生成 MD/HTML/JSON 的 Blob；不再创建分析任务。

主调用入口：[ScoreLab.tsx:222](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:222)、[api-client.ts:195](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:195)、[analysis-session.ts:25](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-session.ts:25)、[LiveResults.tsx:88](C:/Users/Admin/Documents/网球/scoring-demo-web/app/LiveResults.tsx:88)。

## 5. 断点续传的细节

实现入口 [resumable-upload.ts:24](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:24)。

### 5.1 如何找到旧上传

指纹输入为 `JSON.stringify({name,size,options}) + 文件前64KiB + 文件后64KiB`，做 SHA-256。localStorage key 为 `rallymate.upload.v1.{signature}`，value 为 upload UUID。相同文件名/尺寸/首尾/参数会找到同一个候选 session。[resumable-upload.ts:47](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:47)

这个小指纹不能证明文件中间未改变。因此拿到服务器已接收 `received_chunks` 后，浏览器逐个重新计算本地相应分块 SHA-256，与 `chunk_sha256` 比较；任一个不一致就换 UUID 建新会话，避免错误拼接。服务器 chunk_size 决定切块大小，客户端并非永远写死 4 MiB。[resumable-upload.ts:86](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:86)

### 5.2 如何并行和确认进度

两个 `send()` 共享 pending 数组游标，每次取一个缺失 index；块请求携带 `X-Chunk-SHA256`。进度只根据收到服务端确认的 `received` 集合累加字节，不根据 `fetch` 已发出的字节或动画猜测。最后一块按剩余真实长度计算，百分比不超过 100。速度只计本次新确认字节，续传前已有字节从分子扣除。[resumable-upload.ts:35](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:35)、[resumable-upload.ts:116](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:116)

`acknowledge` 先核对 upload_id、size、chunk_size、chunk_count，再只接纳自己已知哈希且服务器哈希相符的分块。集合只增加不减少，因此两个并行请求先后返回的旧 snapshot 不会把进度倒退。[resumable-upload.ts:108](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:108)

### 5.3 超时、重试和丢响应恢复

- 单次默认 90 秒超时；10 秒仍未结束时显示 waiting。
- 网络错误及 408/425/429/500/502/503/504 可重试；非暂时 API 错误直接失败。
- 最多 5 次尝试，退避 `min(8000,700*2^attempt)`；失败文案说明已上传分块保留 24 小时。这是客户端告知的服务约定，实际保留策略仍取决于后端。
- PUT 响应丢失时，先 GET 会话状态核实服务器是否已持久化；若同哈希块已确认，直接继续，避免无谓重发。
- 任一 worker 失败先设置 stopped，再等两个 worker 的 `allSettled` 都结束才向上抛错，避免用户重试时上一轮另一个请求仍在发送。
- 所有块确认后阶段变 merging，POST `/complete`；成功才删除 localStorage 会话指纹。

代码：[resumable-upload.ts:55](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:55)、[resumable-upload.ts:127](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:127)、[resumable-upload.ts:141](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:141)。刷新页面后原生 File 对象不会被持久化，用户仍需重新选择本地文件，之后才能依据指纹续传。

## 6. 轮询、进度、取消与异常恢复

`watchAnalysis` 没有“轮询 180 次就放弃”的总时长上限；只要服务正常返回非终态，就持续观察。暂时错误累计 30 次后退出为可恢复错误，一次成功就重置失败次数。重连间隔默认 2、4、8、15、15…秒；401/403/404 等非暂时错误立即结束。[analysis-session.ts:15](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-session.ts:15)、[analysis-session.ts:35](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-session.ts:35)

后端 `succeeded/completed` 是成功终态；`failed/cancelled` 是失败终态。任务成功不等于报告已经到屏幕：主结果仍需读取，最多重试 3 次。只有 `primary.job_id===jobId` 且 `primary.status==='ready'` 才接受；轨迹和技术评价是可选增强，8 秒超时或失败不清空主结果。最终轨迹失败时可保留同任务运行中预览，同时显示增强证据缺失提示。[analysis-session.ts:79](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-session.ts:79)

实时轨迹预览只在 running/processing 时启动，每 15 秒最多一次，同一时刻只有一个；processed_frames 未改变时不重复请求；请求 `sample_limit=128,prediction_horizon_ms=0`。完成后正式轨迹请求样本上限 384，仍关闭外推。结束观察会 abort 预览子控制器，旧预览不能覆盖最终结果。[analysis-session.ts:55](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-session.ts:55)

`analysis-progress.ts` 将服务端 phase 映射为中文阶段：加载模型→识别→准备回放→跟踪→挥拍识别→事件→特征→评分→保存→生成报告→就绪校验。百分比直接取服务端值，不伪造平滑增长，不以高百分比推断完成；超过 60 秒没更新时间仅提示 stale，不臆造失败。耗时使用 started/created，终态用 completed/updated，不把推理耗时当视频时长。[analysis-progress.ts:3](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-progress.ts:3)、[analysis-progress.ts:31](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/analysis-progress.ts:31)

**取消的实际边界**：当前页面没有“取消后台推理”按钮，API client 没有 cancelJob，Worker 也没有允许 cancel 路由。AbortController 仅停止本次浏览器上传/观察/预览；服务器已提交的任务会继续。页面能显示服务端传来的 cancelled 状态，但不意味着它能从 UI 主动取消服务器任务。离开整个页面会终止观察，切到 overview 等内部视图则保留 ScoreLab 观察。[api-client.ts:83](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:83)、[gateway.ts:45](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/gateway.ts:45)、[ScoreLab.tsx:206](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:206)

恢复按钮按情况选择：有 jobId 且非 failed/cancelled → 继续读取同任务；否则仍有本地文件 → 重试分析；再否则 → 选择新视频。[ScoreLab.tsx:375](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:375)

## 7. API client、代理和服务端路由

### 7.1 API client 的配置与安全边界

`getApiConfig` 支持 NEXT_PUBLIC_* 和 VITE_* 的 base URL/path 配置，跳过空字符串。默认 baseUrl 为空，即浏览器同源；默认 jobsPath `/v1/jobs`、techniquesPath `/v1/techniques`、scorecardPath `/api/scorecard`。这些公开变量不承载后端密钥。[api-client.ts:24](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:24)

后端可能返回带私有主机的绝对结果链接。`urlFor` 只接受路径以 `/v1/` 开头的 HTTP(S) 绝对链接，丢弃其域名，保留 path/query，再拼配置的浏览器 gateway；防止浏览器直接追随分析机器地址。响应必须是对象 JSON；错误支持 `{error}` 或 FastAPI `{detail:{message_zh,message,code}}`，统一变 `RallyMateApiError`。[api-client.ts:116](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:116)

通用 GET 加 `X-Request-ID`、`cache:no-store`、20 秒超时。**只有 getJob、getDemoResult、getTechniqueAssessment 三个报告读取追加 `report_contract=current`**，保持查询和 fragment 的位置；meta/catalog/pose/trajectory/review 不加这个标记。主结果进入 normalizeMeasurementResult。[api-client.ts:160](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:160)、[api-client.ts:196](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:196)

### 7.2 端点使用清单

| 路径 | 浏览器调用目的 | 当前主要调用方 |
|---|---|---|
| POST `/v1/uploads` | 创建/恢复分块会话 | uploadResumable |
| PUT `/v1/uploads/{id}/chunks/{index}` | 发分块和 SHA-256 | uploadResumable |
| GET `/v1/uploads/{id}` | 丢响应后查询已确认块 | uploadResumable |
| POST `/v1/uploads/{id}/complete` | 合并并提交分析 | uploadResumable |
| GET `/v1/jobs/{id}?report_contract=current` | 任务状态 | watchAnalysis |
| GET `/v1/jobs/{id}/demo-result?report_contract=current` | 必需的主报告 | watchAnalysis |
| GET `/v1/jobs/{id}/trajectory` | 实时及完成后的球轨迹 | watchAnalysis |
| GET `/v1/jobs/{id}/technique-assessment?report_contract=current` | 技术证据目录 | watchAnalysis |
| GET `/v1/jobs/{id}/pose-preview` | 10 秒骨架窗口 | ReplayPanel/usePosePlayback |
| GET `/v1/jobs/{id}/artifacts/annotated.mp4` | 视频 Range 回放 | HTML video |
| GET `/v1/jobs/{id}/artifacts/preview.jpg` | 封面与历史缩略图 | ReplayPanel/ReportPreview |
| GET `/v1/techniques` | 技术目录版本等 | ScoreLab 初始化 |
| GET `/v1/meta` | 服务信息；客户端有方法 | 当前 ScoreLab 未调用 |
| GET/POST `/v1/jobs/{id}/technical-review`，GET `/export` | 人工评审台账读写/导出 | 客户端能力存在；当前页面没有调用表单 |
| GET `/api/scorecard` | 本地静态动作目录 | 独立能力接口 |
| POST `/api/scorecard` | 旧接口停用，410 | 不用于真实报告 |
| POST `/api/advice` | 训练问答 | ScoreLab.askCoach |

方法实现索引：[api-client.ts:183](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:183)。

### 7.3 开发代理与生产 gateway

Vite 开发环境将 `/v1` 和 `/health` 转发到 RALLYMATE_API_PROXY / RALLYMATE_API_ORIGIN，默认 `http://127.0.0.1:8000`；需要鉴权时由开发服务器注入 Bearer。配置代码会读取 .dev.vars 和 Vite env，但本次梳理没有打开这些文件。[vite.config.ts:38](C:/Users/Admin/Documents/网球/scoring-demo-web/vite.config.ts:38)

生产 `worker/index.ts` 先处理 `/_vinext/image`，再将 `/v1/` 和 `/health/` 转给 proxyAnalysis，其余交给 vinext App Router。Node production 无 Cloudflare bindings 时，从 process.env 只复制已列举的服务端变量，与显式 env 合并；然后赋给全局 `__env` 供 advice route 读取。[worker/index.ts:33](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/index.ts:33)

gateway 有显式路由白名单，不是开放转发：job ID 8～80 位；只允许列出的只读结果/附件、上传、旧 POST jobs、technical-review POST。POST/PUT 校验 Origin 和 sec-fetch-site；远程网站一般要求上游 HTTPS 且非 loopback，显式本地隧道允许 HTTP loopback 特例。只转发 content-type/accept/range/if-range/x-request-id/x-chunk-sha256，不沿用浏览器 Authorization/Cookie；上游 Bearer 由服务端配置提供。禁止上游重定向。[gateway.ts:42](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/gateway.ts:42)

chunk gateway 的最大体积为 4 MiB：先检查 Content-Length，再实际读取到固定缓冲，防伪造长度或无限小 chunk 占用无界内存。完整读取后设置准确 content-length 再送上游，不把未完整接收的流转成已持久化块。接收+转发共用 80 秒截止，早于浏览器 90 秒截止，使浏览器能够正常进入恢复重试。其他写操作 100 秒、读操作 30 秒。返回内容设置 private/no-store/nosniff，保留 Range 和下载相关头。[gateway.ts:6](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/gateway.ts:6)、[gateway.ts:69](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/gateway.ts:69)

## 8. 导入、数据来源与降级路径

`importSummary` 最大允许 5 MiB，先清空本地视频与旧任务观察，再解析 JSON。它支持三种入口。[ScoreLab.tsx:260](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:260)

1. **本站备份 envelope**：`schemaVersion=rallymate-practice-report/1` 且有效 jobId。优先读备份内 result/assessment/trajectory；只要内嵌结果或技术评价存在就直接恢复完成视图，离线可看。没有内嵌结果才重新观察服务器 job。[ScoreLab.tsx:274](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:274)
2. **单独技术评价或包装技术评价**：从 root.assessment、root.technique_assessment、root.result.technique_assessment 或 root 本体识别，最低形状要求 techniques 数组、assessment/schema version 和 registry version；构造最小 result。不是所有导入字段都经过通用深层 schema 校验，真正严格的业务字段在后续 helper 分别检查。[ScoreLab.tsx:55](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:55)、[ScoreLab.tsx:298](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:298)
3. **Stage 1 summary**：需要 job_id 和 coverage 或 processing，拒绝显式未完成/失败 summary；由 measurementResultFromSummary 转为真实数据视图。保留坐标版本、相机运动和时间戳警告，最多从 event_counts 映射 FS01/FS02/FS09 候选次数，不把覆盖率制造为得分，不填不存在的逐指标特征。[ScoreLab.tsx:318](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:318)、[measurement-evidence.ts:32](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/measurement-evidence.ts:32)

各种 fallback 的含义不同：可选轨迹失败 → 留主结果；技术评价失败 → 使用主结果嵌入的 technique_assessment；视频缺失 → 保留坐标/测量视图；服务端 analysis_report 不合约 → 本地基于实际测量生成复核/补录重点；缺少评分解释 → 显示历史报告无明细；LLM 不可用 → 显式静态说明。没有任何一步会用旧演示分顶替用户视频结论。

## 9. 报告显示与业务派生逻辑

### 9.1 LiveResults 组装顺序

1. normalizeMeasurementResult；
2. summarizeActionCandidates 和 motionAnalysisOf；
3. 默认动作分类取第一类有 motion episode 的 family，之后才候选 family，最后 footwork；
4. 汇总 training/action indicators 并按 indicator_id 去重；
5. 决定 family 下选中指标或 technique；
6. analysisPresentation 区分排队、分析、读取报告、失败、取消、不支持、完成；
7. 主区显示 Replay + TrainingReportOverview；
8. 有完成结果时显示 SourceMeasurementPanel；折叠显示 ScoreExplanationPanel + RuleReviewPanel；
9. 时间线常驻；测量细节折叠，内部显示统计、动作卡、类别轨道、运动/步伐详情和指标 inspector。

源码：[LiveResults.tsx:88](C:/Users/Admin/Documents/网球/scoring-demo-web/app/LiveResults.tsx:88)。`catalog` 作为 prop 传入，但当前 LiveResults 的实现没有使用它构造显示列表；实际 technique 行来自 assessment，原文目录来自本地 scoring-reference.json。

### 9.2 不同“数量”不能互换

`summarizeLiveStats` 的 actionSegments 是 action.detected_segments 的非负整数和；球检测帧/球拍检测帧来自 summary.counts；processedFrames 来自 summary.processing 或 trajectory.source。只有 `hit_statistics.status=observed` 且 `count_semantics=confirmed_contact_events` 才显示 total_count 为已确认触球；没有字段时隐藏击球计数，不从球点/GS 候选递归猜测。[live-stats.ts:59](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/live-stats.ts:59)

### 9.3 训练概览、重点与时间线

`analysisReportOf` 要求精确 training-report-v1.0.0、证据质量分语义、technical score=null、建议语义为 evidence-linked review/capture，并拒绝声称技术错误诊断的 focus。重点最多 3 条。优先采用服务端重点，回放按钮只在其时间窗被实际已验证动作区间覆盖时出现。[training-report.ts:12](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/training-report.ts:12)

没有合约报告时，`reportFocus` 基于真正可测步伐指标和有效转体证据，生成“先回看”或“补足画面”两类提示。`reportMoments` 合并有效 FS 步伐区间和 motion episode，按 startMs 排序；ActionTimeline 每页 6 条，支持全部/步伐/挥拍与转体过滤；选择后发统一 seek 事件。[training-report.ts:36](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/training-report.ts:36)、[ActionTimeline.tsx:7](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ActionTimeline.tsx:7)

概览优先显示服务端独立测量摘要中的“步伐单项观测”和“转体局部窗口”；否则显示可测指标数/总数。被截断时明确只统计已返回片段，参考分单列且说明不等于技术水平。[TrainingReportOverview.tsx:7](C:/Users/Admin/Documents/网球/scoring-demo-web/app/TrainingReportOverview.tsx:7)

### 9.4 运动、阶段与转体

`motionAnalysisOf` 从 result.action_recognition → assessment.action_recognition → summary.action_recognition 取第一个非 nullish 数据源，不把多个来源拼成完整证据。接受 schema 1.0.0/1.1.0，要求 contact_confirmed=false。只支持 baseline/serve/return 三类；逐 episode 检查 family、ID 去重、start≤peak≤end、end>start、规则推断/未分类标记；阶段必须在 episode 内，缺阶段保留 unavailable 和 null 时间。[motion-analysis.ts:8](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/motion-analysis.ts:8)

基础运动量为手腕峰值速度、手腕路程、肘角变化和肩线变化。转体模块支持肩线变化、髋线变化、肩髋最大夹角/夹角变化、肩/髋峰值角速度。每个转体指标独立要求：样本覆盖≥80%，时间覆盖≥80%，连续样本≥7，持续≥200ms，计数相互一致，时间位于 episode 内，明确二维、非正式评分、score=null。不通过时只清空该量，不把 sibling 已测量值一起删除。[motion-analysis.ts:63](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/motion-analysis.ts:63)

局部窗口和分阶段窗口还要求 scope、`source_version=image-plane-rotation-v1.1.0`、`view_semantics=image_plane_proxy`、value 等于 metrics 对应值。整段覆盖不足并不抹除局部可靠窗口，但局部值不会提升成完整三维转体结论。[motion-analysis.ts:83](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/motion-analysis.ts:83)

`MotionAnalysisPanel` 每次显示一段，上下段切换；可用阶段能定位，缺失阶段按钮禁用。`RotationAnalysisPanel` 逐量显示值/缺失原因、有效采样比和真实测量窗定位，另外折叠展示局部/阶段测量。[MotionAnalysisPanel.tsx:10](C:/Users/Admin/Documents/网球/scoring-demo-web/app/MotionAnalysisPanel.tsx:10)、[RotationAnalysisPanel.tsx:4](C:/Users/Admin/Documents/网球/scoring-demo-web/app/RotationAnalysisPanel.tsx:4)

### 9.5 步伐逐段与原文对应实际测量

`footworkEpisodes` 只接受 available 的 1.0/1.1 review，事件代码 FS01/FS02/FS09，严格校验时间和去重；指标 ID 必须与事件前缀一致。新版 independent measurements 每项独立校验 status/value/confidence/unit/view_semantics，测量窗必须精确等于 event interval；单项缺失不影响 sibling。旧版仅在 feature_status=measured 时保留有效 features。[footwork-review.ts:5](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/footwork-review.ts:5)

`SourceMeasurementPanel` 展示更严格的 source_aligned_assessment：先从静态规则逐项找可验证窗口，默认优先有实测的指标和片段；“有测量/未测得/全部”三类过滤，默认最多 3 段。显示真实测量区间、跨事件区间或同帧衔接，而不是拿整个候选窗冒充计算窗。[SourceMeasurementPanel.tsx:14](C:/Users/Admin/Documents/网球/scoring-demo-web/app/SourceMeasurementPanel.tsx:14)

`sourceReviewWindows` 要求 source-aligned-assessment-v1.0.0、非技术等级语义、technical_grade=null、已知视频时长、唯一 indicator 行；可见性必须提供与 valid/total 精确相符的比率、70% 阈值和所需 joints。两个转接毫秒量在 measured value=0 时允许 start=end 表示“同帧衔接”。coverage 区分全量计数与只返回部分窗口的计数，防止把截断当未发生。[technical-review.ts:120](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/technical-review.ts:120)、[technical-review.ts:166](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/technical-review.ts:166)

## 10. 视频、球轨迹、骨架的同步

### 10.1 选择回放源

ReplayPanel 优先看 result.artifact_urls 是否声明 annotated.mp4；有就拼同源 `/v1/jobs/{job}/artifacts/annotated.mp4`，不直接跟随上游 URL。没有服务端视频且本地视频绑定当前 job 时用本地 object URL。封面同样通过同源 artifact。服务端视频播放失败时清空本次 videoSrc，保留测量/轨迹；当前没有再自动降级到本地原片的第二次选择逻辑。[ScoreLab.tsx:74](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:74)

本地原片时间原点为 0；服务端视频使用 trajectory.source.start_timestamp_ms。统一转换：`sourceMs = originMs + currentTimeSeconds*1000`；seek 反向计算并至少为 0。[trajectory-viewer.ts:104](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/trajectory-viewer.ts:104)

### 10.2 播放器交互

默认关闭球轨迹和人体骨架，避免遮挡原片。播放器支持 0.5/0.75/1/1.5/2 倍速、全屏、进度条、播放暂停；真实播放期间用 rAF 同步当前源时间，另监听 video timeupdate/seeking/seeked/loadedmetadata。seek 事件携带 jobId 时拒绝其他任务；元数据未到时将 seek 暂存到 pendingSeekRef。[BallTrajectoryViewer.tsx:81](C:/Users/Admin/Documents/网球/scoring-demo-web/app/BallTrajectoryViewer.tsx:81)、[BallTrajectoryViewer.tsx:159](C:/Users/Admin/Documents/网球/scoring-demo-web/app/BallTrajectoryViewer.tsx:159)

### 10.3 球路画线不是再次推理

优先使用 ball.reconstruction.segments；没有重建才使用 ball.observed 或报告内嵌点。默认只看 moving 段；可选择单段、短尾迹或总览。版本 1.1+ 的重建可作为可信分段几何，最低检测置信度 0.15；旧观测默认 0.6，并以相邻超过 250ms 或跳跃超过归一化 0.18 切断。任何点都须有限、位于 [0,1]，且时间不在当前播放时刻之后。[BallTrajectoryViewer.tsx:178](C:/Users/Admin/Documents/网球/scoring-demo-web/app/BallTrajectoryViewer.tsx:178)、[trajectory-viewer.ts:13](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/trajectory-viewer.ts:13)

每条可显示 run 至少两点且有 0.008 以上移动；segment 模式最近观测最多保留 600ms，tail 默认 750ms，总览保留过去最多 12 条独立候选。并行球候选始终分开，不因“较新”就声称是唯一比赛用球。插值点或穿过插值区间的边画虚线；低置信度仅影响线透明度，不是“正确率”。采样后仍有全段支持的轨迹可避免在稀疏点之间误过期，但不会超过段终点。[trajectory-viewer.ts:20](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/trajectory-viewer.ts:20)、[trajectory-viewer.ts:77](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/trajectory-viewer.ts:77)

### 10.4 骨架按时间窗拉取，不能借用未来帧

`poseReplayTimingAllowed` 只允许本地原片，或 runtime.annotated_video_compatibility.timing_preserved===true 的服务端视频。恒帧率重编码或不明时间基视频不能盲目用源 PTS 叠骨架。[pose-playback.ts:13](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/pose-playback.ts:13)

usePosePlayback 根据当前时间取整到 10 秒窗；每次最多 600 帧；缓存最多 4 个 job+window key；当前窗到达后预取紧接的下一窗；partial 缓存不当作完整缓存复用；换任务/窗口或关闭功能 abort 旧请求。骨架是可选增强，不参与任务完成判断。[use-pose-playback.ts:8](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/use-pose-playback.ts:8)

normalizePosePreview 校验 schema、observed_pose_playback、normalized_frame_0_1、halpe26_named、源时间语义、job 和 10 秒范围。只接受 selected/single_visible_person 的明确选择帧；每关节 confidence≥max(0.35,服务阈值)，关节名白名单、坐标有效；重复关节删除，重复时间帧清空。每帧最多保持 100ms，且不能越过下一帧时间。poseFrameAt 二分查找当前时刻之前的最近帧，只在 valid_until 内返回，不做时间插值、不拿未来帧补骨架。拖动 seeking 期间不画骨架。[pose-playback.ts:18](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/pose-playback.ts:18)、[pose-playback.ts:58](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/pose-playback.ts:58)、[BallTrajectoryViewer.tsx:242](C:/Users/Admin/Documents/网球/scoring-demo-web/app/BallTrajectoryViewer.tsx:242)

## 11. 参考分解释与原文核验

`ScoreExplanationPanel` 不在前端重新发明分数，而是消费后端保存的 score_explanation 并做完整算术对账。五个分量：可测片段比例、必需特征覆盖、特征置信度、跨片段重复性、评分证据覆盖。[score-explanation.ts:6](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/score-explanation.ts:6)

对每个指标：检查 exact version/semantics/aggregation/rounding_method；五个分量不重不漏；included 分量 `max=weight*100`、`earned=valuePercent*weight`、`deduction=max-earned`；excluded 分量数字必须全 null；权重之和为 1；实得之和等于 raw；raw+rounding 等于父分数；并与源 components/effective_component_weights 二次核对。特征缺口和 blocker 数量也要闭合，不能同一个 blocker 重复或超过候选数。[score-explanation.ts:57](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/score-explanation.ts:57)

总分：对所有可用指标参考分等权平均，再校验指标取整影响和总分取整影响；使用 Python ties-to-even 语义验证半整数，不能用 JavaScript Math.round 改分。算术误差仅给 1e-7 容差，输入百分比依然 0～100。未参与项目不补 0；原因计数可重叠，不能把数量直接加成扣分。[score-explanation.ts:141](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/score-explanation.ts:141)

UI 状态分 missing/invalid/verified。只有 verified 才画完整差额账本；missing 显示历史报告未保存明细；invalid 显示未能对账。可以保留独立声明合法的总参考分，但不展示未核实扣分拆解。按总分影响排序显示指标，默认前 5 项，可展开；回放例子最多 3 段，必须来自该指标的真实测量区间，不能从聚合 median 推造时间戳。[ScoreExplanationPanel.tsx:36](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreExplanationPanel.tsx:36)、[score-explanation.ts:194](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/score-explanation.ts:194)

`RuleReviewPanel` 把 298 项原文规则与本次结果并排：关联二维测量/全部原文、动作过滤、搜索、每页 8 项。原文冲突优先标“原文待核实”；未实现则说明尚未接入，不等于 E；有相关数值也不代表完整满足原文判据。展示原文定义、计算方式、不可评价条件、所需观察点、Word 段落/表单元格来源；A～E 标准仅展示定义，不自动赋给视频。[rule-review.ts:35](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/rule-review.ts:35)、[RuleReviewPanel.tsx:14](C:/Users/Admin/Documents/网球/scoring-demo-web/app/RuleReviewPanel.tsx:14)

## 12. 人工技术评审能力的真实接入范围

`technical-review.ts` 和 API client 已实现完整台账契约：job、revision、视频范围、players、events、规则、视觉条款、评审条目，以及 source/artifact/reference 三类哈希绑定。台账必须 `semantics=human_source_rule_review`、formal_grade=null、calibration_eligible=false，人工记录不自动变模型正式分。[technical-review.ts:11](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/technical-review.ts:11)

`parseTechnicalReview` 校验整账结构、ID、人物事件关系、来源是否仍当前；历史过期来源可保留，但当前条目必须匹配当前哈希。`technicalReviewError` 校验保存输入 revision、mutation UUID、时间范围、人物有效期、系统事件对应性、评审人和理由。视觉要点只能 observed/not_observed/unassessable，没有等级；指标只有来源允许且 observable 时可人工 A～E；修订不能偷偷更改人物、时段、来源或评审人。[technical-review.ts:43](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/technical-review.ts:43)、[technical-review.ts:89](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/technical-review.ts:89)

当前 UI 使用此模块的 sourceReviewWindows 等只读测量函数，**没有人工等级保存表单**；get/save/exportTechnicalReview 只在客户端实现和测试中被引用。不能把这些底层能力描述成当前用户已经能在网页上提交教练评级。相应测试专门确认 source measurement 页面没有手工评分控件。[technical-review.test.mjs:165](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/technical-review.test.mjs:165)

## 13. 训练问答与 MiMo 的实际角色

### 13.1 浏览器行为

askCoach POST `/api/advice`，传 technique、固定 skillLevel=业余进阶、固定 sessionGoal=稳定性、用户 observations 和可选 jobId。问题最多 600 字。结果按 `[jobId,technique]` 作为 contextKey 保存，切换任务或主题不会显示上一任务建议。当前 key 不含问题文本，意味着同任务同主题下提交新问题会替换旧建议，而不是多轮聊天历史。[ScoreLab.tsx:245](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ScoreLab.tsx:245)、[advice-display.ts:6](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/advice-display.ts:6)

### 13.2 服务端处理顺序

1. 只收 application/json；Origin 和 cross-site 检查。
2. 以 cf-connecting-ip 或 anonymous 做进程内 10 分钟 5 次限流，最多 2000 个桶；实例最多 2 个并发请求。
3. 读取正文上限 8000 字节、读取超时 10 秒；字段精确白名单，枚举验证，拒绝控制字符、敏感凭据/PII 模式和注入词。
4. 若 observations 触发身体不适模式，直接 safety_fallback，不请求模型。
5. 有 jobId 时由服务器根据 RALLYMATE_API_ORIGIN 自己取 `/demo-result`，6 秒超时、上限 512000 字节，并验证 job/status；不相信浏览器提交“我的视频得了90分”等自述。
6. 针对请求 family 构造证据；不足则 evidence_fallback，不耗模型请求。
7. 有可解释证据且配置允许时调用 MiMo；校验模型结果；失败显式 fallback。

入口：[advice/route.ts:216](C:/Users/Admin/Documents/网球/scoring-demo-web/app/api/advice/route.ts:216)、[advice/route.ts:167](C:/Users/Admin/Documents/网球/scoring-demo-web/app/api/advice/route.ts:167)。限流 Map 与 inFlight 在模块/实例内，不是分布式数据库全局限流。

### 13.3 模型不是自由写训练诊断

advice-evidence 的状态为 no_video / analysis_unavailable / insufficient_evidence / motion_only / observed。只取所选 family；高步伐参考分不能证明底线技术。observed 也只是候选事件可复核；阶段只认 measured + explicit_phase_record。motion_only 要求规则 motion episode 合法、有姿态采样和有限测量，阶段缺失必须保留，不能拼不同片段成为完整动作。[advice-evidence.ts:69](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/advice-evidence.ts:69)

MiMo 只能返回两个 ID 数组：`evidenceFactIds` 和 `nextStepIds`。所有事实和建议文字由服务器预先生成；模型只是选择最相关的事实与获准提示。motion_only 只允许 replay/recording 两类提示；不接受模型自由书写摘要、分数、训练方案、触球结论或诊断。验证后服务端组合最终 Advice，并始终保持 drills=[]。[advice/route.ts:131](C:/Users/Admin/Documents/网球/scoring-demo-web/app/api/advice/route.ts:131)、[advice/route.ts:181](C:/Users/Admin/Documents/网球/scoring-demo-web/app/api/advice/route.ts:181)

配置必须显式 `MIMO_ADVICE_ENABLED=1`；单独存在 API key 不启用付费调用。provider 只支持 openai/anthropic 两种协议，目标固定为小米 MiMo 官方付费 API 两个 canonical endpoint；拒绝任意 base URL、Token Plan host/key。默认模型 mimo-v2.5-pro，25 秒超时，最多 500 tokens，模型响应正文上限 64000 字节。[mimo-config.ts:20](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/mimo-config.ts:20)、[advice/route.ts:187](C:/Users/Admin/Documents/网球/scoring-demo-web/app/api/advice/route.ts:187)

一个实际版本边界：页面 motionAnalysisOf 接受 1.0.0/1.1.0，而 advice-evidence 的 validMotion 目前只认 1.0.0。因此同一份 1.1.0 新运动报告可以在页面展示，但如果没有 observed technique 补充路径，问答可能保守降级为证据不足。这是当前实现差异，不是本次网络调用得出的运行结论。[motion-analysis.ts:11](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/motion-analysis.ts:11)、[advice-evidence.ts:93](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/advice-evidence.ts:93)

## 14. 报告导出的完整约束

导出完全在浏览器本地完成。先由 `getReportExportGate` 检查：不能 uploading/processing/ready 或 live-pending；不能 error/failed/cancelled；真实任务必须 complete 且有实际分析内容；job、evidence、result、assessment、trajectory 的所有有效 ID 不得互相冲突。可选轨迹缺失不阻止导出，但写进限制。[report-export.ts:98](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/report-export.ts:98)

`buildPracticeReport` 生成统一可读结构，再由 Markdown/HTML renderer 渲染。内容包括任务和真实视频元数据、可对账分数解释、原文核验范围、原文对应测量、复核重点、分析层可用性、历史坐标警告、动作观察、技术证据覆盖、运动阶段、可测指标、转体局部窗、步伐逐段、球路概览和限制。[report-export.ts:174](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/report-export.ts:174)

文字过滤服务器路径、链接、凭据、控制字符；HTML 转义全部文本，CSP 禁止脚本、联网、图片和表单，提供独立样式与打印 A4 CSS。所谓 PDF 导出是“打开 HTML 后浏览器打印保存 PDF”，没有直接生成 PDF 的前端方法。[report-export.ts:47](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/report-export.ts:47)、[report-export.ts:334](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/report-export.ts:334)

`buildReportBackup` 保留兼容导入 envelope `rallymate-practice-report/1`，使用字段白名单选择 result/assessment/summary/trajectory，深度最多 16 层 scrub；移除 path/url/token/secret 等字段；唯一特例 wrist_path_torso 是运动数值名称，保留它。技术/正式等级强制 null；只有明确证据分语义或完整账本验证过的子分值可保留。视频仅保留 duration_ms、width、height、frame_count，没有原片/访问凭据/远程资源 URL。[report-export.ts:340](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/report-export.ts:340)

下载使用 Blob→object URL→临时 anchor.click→1 秒后 revoke。当前可读报告的“原文核验范围”文字内有固定 7/298/24/13 数字，sourceRows 特别列了 4 个 source-aligned ID；这些是实现内固定快照，并非全部动态读 catalog 的统计。JSON 备份的 sourceAssessmentForExport 则遍历全部合法 indicator。[ReportExportActions.tsx:9](C:/Users/Admin/Documents/网球/scoring-demo-web/app/ReportExportActions.tsx:9)、[report-export.ts:224](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/report-export.ts:224)

## 15. 三份静态数据与旧评分模块

| 文件 | 当前实际内容 | 用途 |
|---|---|---|
| metric-cards.json | 298 cards；registryVersion=2026-08-10-demo.1；单行 JSON | page 向 ScoreLab 传版本/来源统计；保留旧指标卡格式 |
| technique-catalog.json | 24 techniques；5 source documents；registry_version=2026-09-12.1 | `/api/scorecard` GET 本地目录；不等同运行中后端 `/v1/techniques` |
| scoring-reference.json | 7 份文档、298 rules、24 visual_techniques；13 rules 为 related_2d_measurement；单行 JSON | LiveResults 的原文核验和 source measurement 规则目录 |

来源：[metric-cards.json:1](C:/Users/Admin/Documents/网球/scoring-demo-web/app/data/metric-cards.json:1)、[technique-catalog.json:1](C:/Users/Admin/Documents/网球/scoring-demo-web/app/data/technique-catalog.json:1)、[scoring-reference.json:1](C:/Users/Admin/Documents/网球/scoring-demo-web/app/data/scoring-reference.json:1)。单行 JSON 只能定位到第 1 行，进一步定位要按 id 搜索。

旧 engine 的计算仍值得知道，以免后来维护误接：

- evidenceFor：必需依赖取最小覆盖率，可选依赖取均值；`evidence=mandatory*0.85+optional*0.15`；必需依赖<0.65 视为缺失。
- real mode 只给 ready/partial/blocked，score、grade、dimensions 一律 null；证据≥0.72 且无缺失才 ready，≥0.4 为 partial。
- demo mode 用 scenario baseScore + 文本关键词强调 + FNV 类 hash 确定性 jitter 构造四个维度；维度权重 40%/25%/20%/15%；等级阈值 90/80/70/60。
- 指标→事件等权平均，再事件→GS/FS 模块平均，模块按 GS70%/FS30% 合成；必须两模块都有分才有 overall。
- scenarioFromStage1Summary 优先主球员 pose/tracking 诊断，否则全局覆盖作为上限代理；baseScore=null、eventConfidence=0，real mode 不出技术分。

来源：[engine.ts:104](C:/Users/Admin/Documents/网球/scoring-demo-web/app/scoring/engine.ts:104)、[engine.ts:146](C:/Users/Admin/Documents/网球/scoring-demo-web/app/scoring/engine.ts:146)、[engine.ts:189](C:/Users/Admin/Documents/网球/scoring-demo-web/app/scoring/engine.ts:189)、[engine.ts:303](C:/Users/Admin/Documents/网球/scoring-demo-web/app/scoring/engine.ts:303)、[engine.ts:350](C:/Users/Admin/Documents/网球/scoring-demo-web/app/scoring/engine.ts:350)。

`scenarios.ts` 有两个合成演示场景（baseScore 91/72）和四个历史 FULL-TEST 真实覆盖快照，描述的是固定历史数据，不是当前接口实时结果。当前生产页不展示该场景切换器。[scenarios.ts:3](C:/Users/Admin/Documents/网球/scoring-demo-web/app/scoring/scenarios.ts:3)

## 16. 其余支撑文件、部署和身份边界

- `api-types.ts` 统一 TS 契约：JobSubmission/Progress、DemoResultResponse、AnalysisReport、MotionEpisode/Rotation、FootworkReview、Indicator/TrainingEvaluation、Trajectory/Pose/TechniqueAssessment。它允许多个 `[key:string]:unknown` 兼容扩展，静态类型不是运行时 JSON 验证；严格验证由各业务 helper 单独完成。[api-types.ts:111](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-types.ts:111)
- `AnimatedDisclosure` 包装原生 details/summary，实现可中断高度动画，快速反向点击从当前高度继续；减少动态效果时直接 settle，保留原生 onToggle 和可访问性。[AnimatedDisclosure.tsx:14](C:/Users/Admin/Documents/网球/scoring-demo-web/app/AnimatedDisclosure.tsx:14)
- `workspace-navigation` 还提供报告日期和视频时长工具：日期只接受带时区 ISO，优先 completed，再 created，明确“分析完成/提交时间”；时长只读 input.video/metadata.duration_ms，不从帧数/处理耗时推断。[workspace-navigation.ts:48](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/workspace-navigation.ts:48)
- `.openai/hosting.json` 当前 d1/r2 都是 null。db/schema.ts 默认空，getDb 要求绑定 DB；业务页面没有调用 getDb。examples/d1 是可选示例，不是训练记录存储。[hosting.json:1](C:/Users/Admin/Documents/网球/scoring-demo-web/.openai/hosting.json:1)、[db/schema.ts:1](C:/Users/Admin/Documents/网球/scoring-demo-web/db/schema.ts:1)
- `chatgpt-auth.ts` 是模板身份 helper，解析网关注入用户头和安全的登录/退出返回路径；当前业务 page/ScoreLab 没有 requireChatGPTUser 调用。训练网站公开访问不要求浏览器登录，后端 API 服务密钥仍在 gateway 注入，二者是不同边界。[chatgpt-auth.ts:21](C:/Users/Admin/Documents/网球/scoring-demo-web/app/chatgpt-auth.ts:21)、[rendered-html.test.mjs:54](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/rendered-html.test.mjs:54)
- `sites-vite-plugin.ts` 是 vendored Sites 模板插件：开发时只对 loopback 的本地请求提供 mock sign-in；先清除伪造的 oai-authenticated-user-*；构建结束把 hosting 配置和可选 drizzle 目录复制到 dist/.openai。它不运行视频分析。[sites-vite-plugin.ts:32](C:/Users/Admin/Documents/网球/scoring-demo-web/sites-vite-plugin.ts:32)、[sites-vite-plugin.ts:173](C:/Users/Admin/Documents/网球/scoring-demo-web/sites-vite-plugin.ts:173)
- TS strict/noEmit，React 19.2.6、vinext 1.0.0-beta.2、Vite 8.0.13；Node 要求≥22.13.0。启动部署必须同时考虑网页服务端和独立 Python origin，不能只 build 静态页面就认为推理服务已部署。[package.json:5](C:/Users/Admin/Documents/网球/scoring-demo-web/package.json:5)、[tsconfig.json:7](C:/Users/Admin/Documents/网球/scoring-demo-web/tsconfig.json:7)

## 17. 测试覆盖到了哪里，没证明什么

package 的 `npm test` 先 `vinext build`，再 `node --test tests/*.test.mjs`；typecheck 和 lint 是单独脚本。[package.json:8](C:/Users/Admin/Documents/网球/scoring-demo-web/package.json:8)

大量测试用 TypeScript transpileModule 将单个 TS/TSX 文件转成 data URL 后导入，注入假的 fetch、localStorage 或 client；组件用 React renderToStaticMarkup 检查输出。它们验证业务契约、语义和渲染标记，通常不运行浏览器 hydration、真实 pointer/seek/网络长视频行为。rendered-html 测试另直接导入构建的 `dist/server/index.js` 并调用 Worker fetch，因此需要先构建。[product-workspace.test.mjs:8](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/product-workspace.test.mjs:8)、[rendered-html.test.mjs:4](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/rendered-html.test.mjs:4)

| 测试组 | 重要断言与边界 | 定位 |
|---|---|---|
| workspace-navigation | 显式 view 优先、无效 job、离开分析去除 job、刷新稳定、日期不是拍摄日期、时长不可猜 | [workspace-navigation.test.mjs:16](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/workspace-navigation.test.mjs:16) |
| product-workspace | 空历史不造报告、历史过滤排序、桌面/移动全部回调、busy 可导航和返回首页、检查清单可选 | [product-workspace.test.mjs:35](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/product-workspace.test.mjs:35) |
| api-client | 三种报告请求才加 current、私有绝对地址重写、查询/fragment、非法 URL 发送前拒绝 | [api-client.test.mjs:40](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/api-client.test.mjs:40) |
| resumable-upload | 两并发、真实字节/SHA、断点只补缺块、中间字节变更、complete 丢响应、服务端128KiB块、410新会话、PUT丢确认、进度单调 | [resumable-upload.test.mjs:111](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/resumable-upload.test.mjs:111) |
| gateway-upload-body | 碎流完整接收、4MiB边界、伪造长度、空流、中断、80秒截止、abort和跨站拒绝；有本地 HTTP upstream 集成测试 | [gateway-upload-body.test.mjs:22](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/gateway-upload-body.test.mjs:22) |
| analysis-session | 超过180轮仍持续、重连30次、401/403/404终止、主结果3次、可选失败保留、停止观察、预览不阻塞/不串任务 | [analysis-session.test.mjs:20](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/analysis-session.test.mjs:20) |
| analysis-progress/live-stats | 高百分比不等于完成、stale不造失败、completed尚待读结果；检测点和动作片段不转击球数 | [analysis-progress.test.mjs:10](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/analysis-progress.test.mjs:10)、[live-stats.test.mjs:24](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/live-stats.test.mjs:24) |
| measurement-evidence | 旧分抑制但保留测量、0分合法性、当前语义往返、summary不造分、空/失败摘要 | [measurement-evidence.test.mjs:28](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/measurement-evidence.test.mjs:28) |
| score-explanation | 指标与总分闭合、缺项重分权重、Python半偶取整、浮点噪声、伪造账本拒绝、真实回放区间 | [score-explanation.test.mjs:84](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/score-explanation.test.mjs:84) |
| motion/footwork/training-report | 部分证据独立保留、阶段/窗口校验、无技术分、服务端重点语义、时间线/回放/截断范围 | [training-report.test.mjs:32](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/training-report.test.mjs:32) |
| trajectory/replay/pose | 多轨不串、未来点隐藏、600ms过期、插值虚线、原片时间基、默认无叠加、骨架100ms有效期、错误job/低置信/多主体不补造 | [trajectory-viewer.test.mjs:11](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/trajectory-viewer.test.mjs:11)、[pose-playback.test.mjs:26](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/pose-playback.test.mjs:26) |
| rule/technical-review | 原文冲突、未实现不E、来源哈希/revision/人时段绑定、视觉条款不可赋等级、只读测量无评级表单 | [technical-review.test.mjs:42](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/technical-review.test.mjs:42) |
| advice | 请求family隔离、部分阶段不补全、无视频不调用模型、ID选择器输出、注入/隐私/跨站/限流、provider失败、key不自动启用付费 | [advice-guardrails.test.mjs:53](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/advice-guardrails.test.mjs:53)、[mimo-config.test.mjs:11](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/mimo-config.test.mjs:11) |
| report-export | 未完成/错job/空数据禁止、真实元信息、optional缺失可导出、脚本转义/CSP、凭据路径过滤、JSON往返视频边界 | [report-export.test.mjs:31](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/report-export.test.mjs:31) |
| rendered-html | 构建Worker可渲染、目录24类/5分类、旧POST410、建议fallback、公开上传/服务端鉴权、Node缺bindings适配 | [rendered-html.test.mjs:9](C:/Users/Admin/Documents/网球/scoring-demo-web/tests/rendered-html.test.mjs:9) |

这套测试不能单独证明 GPU 推理正确、真实视频编码全兼容、浏览器完整交互无问题、公网隧道长期稳定、LLM 供应商当前可用、多实例限流有效或用户身份隔离成立；这些需要相应后端/真实浏览器/部署环境验证。本次工作是代码逻辑梳理，没有运行真实推理或外部模型调用。

## 18. 推荐的逐文件阅读顺序

第一次理解主链：`page.tsx → ScoreLab.tsx → api-client.ts → resumable-upload.ts → analysis-session.ts → LiveResults.tsx`。

第二遍理解“显示的每个结论为何可信”：`measurement-evidence.ts → motion-analysis.ts → footwork-review.ts → training-report.ts → score-explanation.ts → rule-review.ts → technical-review.ts`。

第三遍理解交互和外围：`BallTrajectoryViewer.tsx → trajectory-viewer.ts → use-pose-playback.ts → pose-playback.ts → api/advice/route.ts → advice-evidence.ts → report-export.ts → worker/gateway.ts → vite.config.ts`。

维护时尤其留意：任务状态和 UI 状态不同；上传恢复和后台任务恢复不同；候选片段、已测特征、确认触球不同；来源关联、证据参考分和技术评级不同；“实现了函数”与“当前页面已接入”不同。当前代码主要通过这些边界避免将缺失证据包装成完整技术结论。
