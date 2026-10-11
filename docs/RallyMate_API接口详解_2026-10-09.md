# RallyMate API 接口定义与实现详解

本文面向正在阅读 `src/rallymate_service/api.py` 的开发者，解释每个接口为什么存在、请求如何进入 Python 函数、后端做了什么、返回值应如何理解，以及前端应该何时调用它。它既是接口调用手册，也是这份源码的阅读指南。

核对日期为 **2026 年 10 月 9 日**。范围是当前工作区 `api.py` 注册的 **21 个显式 HTTP 路由**，以及决定其行为的上传存储、任务数据库、Worker、报告构造和人工复核代码。接口详细行为以当前源码为依据；示例使用虚构 ID 和数据，只用于说明请求结构，不代表本次执行过上传、推理或线上验证。

文档中的限制值是源码默认值或代码硬限制，**不等于某个正在运行的部署实际配置**。本次没有读取真实密钥或启动推理服务。项目旧文档中“9 个显式路由”的描述对应较早版本，不能用来判断当前接口是否存在。

推荐顺序：先阅读“从一个接口定义读懂 FastAPI”和“公共约定”，再阅读上传与任务接口，最后按需求阅读结果展示、回放、人工复核和文件下载。文末提供前端完整调用顺序、错误定位方法和源码索引。

## 从一个接口定义读懂 FastAPI

以任务查询为例，以下结构来自 [src/rallymate_service/api.py:1043](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1043)，省略了函数体：

```python
@app.get("/v1/jobs/{job_id}")
def get_job(
    job_id: str,
    report_contract: ReportContract = Query(DEFAULT_REPORT_CONTRACT),
    _: None = Depends(authorize),
) -> dict:
    ...
```

这段代码建立了“HTTP 请求 → Python 函数”的对应关系，并不是定义一个普通函数之后由前端直接调用 Python。

| 定义部分 | 含义 | 客户端对应内容 |
| --- | --- | --- |
| `@app.get(...)` | 把下面的函数注册为 GET 路由 | HTTP 方法 GET |
| `/v1/jobs/{job_id}` | 花括号内是路径变量 | 例如 `/v1/jobs/example-job-id` |
| `job_id: str` | FastAPI 从 URL 路径取值，并按声明解析 | 不需要再把 `job_id` 放进请求体 |
| `Query(...)` | 从 URL 的查询字符串读取 | `?report_contract=current` |
| `Depends(authorize)` | 在进入业务处理前执行鉴权依赖 | 通常对应 `Authorization: Bearer ...` |
| 参数名 `_` | 约定表示只需要依赖的执行效果，不使用其返回值 | 客户端不应提交名为 `_` 的字段 |
| `-> dict` | Python 返回值类型提示 | 正常结果由框架转换成 JSON；这不是完整的 JSON 字段模型 |
| `return {...}` | 生成成功响应的数据 | 未特别声明时通常为 HTTP 200 |
| `raise HTTPException(...)` | 中止当前请求，返回指定状态码和错误详情 | 例如 404 或 409 |

路径参数、查询参数、表单和 JSON 请求体是不同输入通道。不能因为它们都在函数括号内，就把所有值都放到一个 JSON 对象里。

| 写法 | 参数从哪里来 | 本项目中的例子 |
| --- | --- | --- |
| 路由 `{name}` 与同名函数参数 | URL 路径 | `job_id`、`upload_id`、`index` |
| `Query(...)` | URL 查询字符串 | `limit`、`sample_limit`、`start_ms` |
| `Header(default=None)` | 请求头 | `authorization` 对应 Authorization |
| `File(...)` 与 `UploadFile` | multipart 文件字段 | `POST /v1/jobs` 的 `video` |
| `Form(...)` | multipart 普通表单字段 | `court_mode`、`write_annotated_video`、`max_players` 等 |
| `payload: dict` | JSON 请求体 | `POST /v1/uploads` |
| `request: Request` | 框架注入的整个请求对象 | 分片原始字节流、人工复核 JSON、来源信息 |
| `response: Response` | 框架提供的响应控制对象 | 设置人工复核响应的 Cache-Control |

`File(...)` 里的省略号表示必填；`Form(2)` 表示不提交时采用默认值 2。`str | None` 表示允许空值，但是否允许省略还要看有没有默认值。`Query(600, ge=1, le=600)` 同时声明默认值和范围。`Literal[...]` 限制可接受的字符串值，例如报告契约只有 `legacy-v1` 与 `current`。

`async def` 允许在读取上传流等操作时使用 `await`；它不意味着这次 HTTP 请求已经变成后台推理任务。视频推理的异步性来自“任务入库后由 Worker 消费”的业务设计。`def` 也可以作为 FastAPI 接口函数，项目中的许多只读接口就是这种写法。

## 这份文件在整个系统中承担什么职责

`api.py` 是 HTTP 边界层：接收并检查请求、执行鉴权、调用具体服务、选择状态码、组织公开响应。视频推理、SQLite 存取、分片合并和人工复核存储分别由其他模块完成。

```text
浏览器页面
  → 同源网关或直接请求 FastAPI
  → 请求标识与鉴权
  → api.py 路由处理函数
      ├─ 发现与就绪接口：读取能力和配置状态
      ├─ 上传接口：写视频与请求记录，将任务放入 SQLite
      ├─ 查询接口：读取任务状态和已生成的产物
      └─ 人工复核接口：把人工意见写入独立的复核账本

Worker 单独消费 SQLite 中的 queued 任务
  → running → 推理、分析、产物校验 → succeeded 或 failed

浏览器轮询 GET /v1/jobs/{job_id}
  → 成功后读取 demo-result、technique-assessment 和 artifacts
```

`create_app()` 初始化配置和服务对象，定义所有路由并返回 FastAPI 应用，见 [src/rallymate_service/api.py:366](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:366)。它本身不是 HTTP 接口。`lifespan()` 在服务启动阶段初始化任务数据库和人工复核数据库，见 [src/rallymate_service/api.py:414](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:414)。文件末尾的 `app = create_app()` 给服务器提供可导入的应用对象。

启动 API 与启动 Worker 不是同一件事。`rallymate-api` 与 `rallymate-worker` 是分开的入口；`rallymate-dev` 才会在本地入口中同时启动一个 Worker 线程，见 [src/rallymate_service/cli.py:15](C:/Users/Admin/Documents/网球/src/rallymate_service/cli.py:15)、[src/rallymate_service/cli.py:32](C:/Users/Admin/Documents/网球/src/rallymate_service/cli.py:32)、[src/rallymate_service/cli.py:49](C:/Users/Admin/Documents/网球/src/rallymate_service/cli.py:49)。因此，“请求能返回 202”只说明任务被接受，不证明后端一定有正在消费任务的 Worker。

## 公共约定

### 地址与版本

本文使用 `BASE_URL` 表示实际 FastAPI 服务地址或允许转发该路由的网关地址。示例中的 `http://127.0.0.1:8000` 只是本地调用示意，未验证该地址当前是否运行服务。

| 标识 | 当前代码值 | 意义 |
| --- | --- | --- |
| URL 前缀 `/v1` | 业务路由主版本 | 不等于某个响应体内部的 schema 版本 |
| `API_VERSION` | `1.3.0` | `/v1/meta` 发布的 API 能力版本 |
| `API_COMPATIBILITY_VERSION` | `1.2.0` | 兼容旧调用方的版本标记 |
| FastAPI / OpenAPI `info.version` | `1.2.0` | 由兼容版本赋值；不要据此推断缺少新接口 |
| `report_contract` | 多数报告查询默认 `legacy-v1` | 控制返回数据的兼容投影，不重新运行模型 |
| 文件下载的 `report_contract` | 默认 `current` | 默认保留原始产物表示，详见下载接口 |

来源：[src/rallymate_service/api.py:77](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:77)、[src/rallymate_service/api.py:420](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:420)、[src/rallymate_service/report_compatibility.py:16](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:16)。

`legacy-v1` 只对已知的部分新版结构进行兼容转换，包括 `footwork_review` 和 `motion_analysis`；不是把所有响应字段统一改成旧版本。`current` 保留当前表示。投影不会创造缺失测量值或正式技术等级，也不会改写磁盘上的原始产物。新前端的 `getJob`、`getDemoResult`、`getTechniqueAssessment` 已显式请求 `current`，见 [scoring-demo-web/app/lib/api-client.ts:196](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:196)。

### 鉴权规则

带 `Depends(authorize)` 的路由共用 [src/rallymate_service/api.py:458](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:458) 的规则。`GET /` 和 `GET /health/live` 没有挂载这一依赖。

| 服务条件 | 受保护接口的行为 |
| --- | --- |
| 配置了 `api_key` | 必须传入精确匹配的 `Authorization: Bearer <key>`；缺失或不匹配返回 401 |
| 非 production 且未配置 `public_base_url`、未配置 key，包括 development/staging/test | `authorize` 直接放行 |
| 生产环境或配置了 `public_base_url`，但未配置 key | `/health/ready`、`/v1/meta`、`/v1/techniques` 可用于启动诊断；其他挂载鉴权的接口返回 503 |

401 响应带 `WWW-Authenticate: Bearer`。未配置公开访问认证时，错误详情是对象：

```json
{
  "detail": {
    "code": "authentication_not_configured",
    "message": "RALLYMATE_API_KEY is required for public API access"
  }
}
```

这套机制是服务共享 Bearer key，不是账号登录或按用户隔离的权限系统。当前这些路由没有用户登录、JWT 签发或“只列出自己的任务”的筛选参数。浏览器端现有代码通过网关注入后端密钥，详见文末的网关差异。

### 请求标识与跨域

`X-Request-ID` 用于跟踪一次 HTTP 请求，**不是任务 ID，也不是幂等键**。客户端可提供符合 `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` 的值；缺失或不合法时后端生成 UUID 十六进制标识。直接访问 FastAPI 时，正常进入路由处理链的响应会带回该标识；经网关时能否看到它，还取决于网关响应头转发规则。来源：[src/rallymate_service/api.py:255](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:255)、[src/rallymate_service/api.py:439](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:439)。

CORS 来源由 `RALLYMATE_CORS_ORIGINS` 配置，允许的方法为 GET、POST、PUT、OPTIONS；允许的请求头包括 Authorization、Content-Type、X-Request-ID、X-Chunk-SHA256；对浏览器暴露 X-Request-ID，不启用跨域 credentials。生产或显式公开部署要求明确来源，不能使用通配符。CORS 是浏览器跨域策略，不替代后端鉴权。来源：[src/rallymate_service/api.py:450](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:450)、[src/rallymate_service/config.py:64](C:/Users/Admin/Documents/网球/src/rallymate_service/config.py:64)。

### 成功状态与错误状态

下面是公共阅读规则，各接口章节会给出自己的分支。特别是 HTTP 成功和业务可用是两个层次：`/health/ready` 的 `not_ready`、部分预览结果的不可用状态，都可能放在 HTTP 200 响应里。

| 状态码 | 本项目中的主要含义 |
| --- | --- |
| 200 | 查询成功、分片写入成功、人工复核保存成功；还需读取响应中的业务状态 |
| 201 | 创建或复用上传会话，由路由装饰器统一设置 |
| 202 | 视频任务已接受；不是推理完成 |
| 401 | 缺失或错误的 Bearer 凭据 |
| 404 | 任务、上传会话、产物不存在，或产物名不在允许列表 |
| 409 | 当前任务状态不允许读取，或分片、复核来源等存在冲突 |
| 410 | 上传会话过期，或尚无数据的旧分块协议会话需要重建 |
| 413 | 视频、分片或人工复核请求体超过限制 |
| 415 | 视频文件扩展名不受支持 |
| 422 | 请求字段/内容非法，或部分产物证据无法解析 |
| 429 | 上传根目录的活跃会话达到上限 |
| 500 | 部分接口显式报告服务端注册表/结果构建问题，或发生未捕获异常 |
| 503 | 注册表或必要配置不可用，或公开服务未配置认证 |
| 507 | 新上传会话的磁盘空间检查不通过 |

错误体没有被整个文件统一成一个固定结构。`HTTPException` 的详情通常出现在 `detail` 下，可能是字符串，也可能是对象；框架参数校验错误还可能是一组错误列表。前端必须按类型读取，不能假设 `detail.message` 总存在。

```json
{"detail": "job not found"}
```

```json
{"detail": {"code": "technique_registry_unavailable", "message": "具体原因"}}
```

上面是两种结构示意。服务内部未被捕获的程序异常仍可能产生 500；文档中的显式错误列表不是对所有底层故障的穷尽承诺。通过前端网关访问时还可能得到 `{"error": "..."}`，不能和 FastAPI 的 `detail` 结构混为一谈。

### 错误类 校验函数 与 HTTPException 的分工

此前讨论的 `TechniqueRegistryError` 是业务异常分类。类继承 `ValueError`，通过父类继承构造、`args` 和字符串化能力；类中的文档字符串不是自动报错提示。真正的错误条件和文案在 `raise TechniqueRegistryError("...")` 处决定，接口层再捕获它并选择 HTTP 状态码。

```python
# 注册表模块：检查服务端数据，不处理 HTTP。
if not isinstance(techniques, list) or not techniques:
    raise TechniqueRegistryError("techniques must be a non-empty list")

# API 层：决定如何对请求方说明失败。以下是简化结构。
try:
    catalog = technique_catalog(...)
except TechniqueRegistryError as exc:
    raise HTTPException(503, str(exc)) from exc
```

`except ... as exc` 中的 `exc` 是某一次具体异常对象；`str(exc)` 取得创建异常时传入的文案；`from exc` 保留内部异常因果链，便于排查，并不意味着把完整堆栈发送给前端。实际目录接口还会给消息加前缀并进行脱敏，见 [src/rallymate_service/api.py:538](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:538)。

Python 类型/范围校验、上传文件有效性校验、服务器注册表校验、结果来源指纹校验分别解决不同问题。它们都发生在后端，不是由前端页面自动完成；前端可以提前提示，但后端仍必须自行检查。

### 公开响应脱敏的范围

满足“生产环境”或“设置了 `public_base_url`”之一时，公开视图会按路由中的调用执行脱敏。`_redact_public_value` 递归遍历字典和列表，将路径类字段改成 `<redacted>`，将 `source_ip`、`client_ip`、`remote_addr` 的值改为 `None`（JSON 中是 null），并处理普通字符串里的本地路径。普通数字和无关文字保持原值。来源：[src/rallymate_service/api.py:122](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:122)。

```python
if isinstance(detail, list):
    return _redact_public_value(detail)
```

这就是“如果错误详情是列表，返回递归脱敏后的列表”。它不会逐个发起 HTTP 请求，也不是在校验前端列表控件。

任务公开视图还会隐藏内部工作目录，并在公开模式下把原始文件名替换为 `video-<任务ID前8位>.<扩展名>`。但**原始产物下载默认保留文件内容，不统一做 JSON 脱敏**。不能因为某个任务 JSON 被脱敏，就推断所有下载文件都被清洗。参见 [src/rallymate_service/api.py:263](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:263) 和文件下载章节。

### 数据表示与评分含义

任务 `status` 表示计算任务生命周期，不表示球员技术水平。`succeeded` 说明这次后台计算及产物处理成功，不保证每项技术都能测量，也不表示正式教练评分已经通过。

技术目录描述“系统如何定义这些动作及所需证据”；技术评估描述“这次视频有什么证据”；`demo-result` 聚合面向展示的报告、解释和参考反馈；人工复核记录人的确认或修正。前端应同时读取可用性、原因和来源字段。`null` 不应被转成零分，候选动作、骨架置信度或 Beta 参考分也不能直接解释为正式 A～E 等级。

## 路由总览

| 方法 | 路径 | 主要作用 | 正常码 | 处理函数 |
| --- | --- | --- | --- | --- |
| GET | [/health/live](#route-01) | 检查 API 进程存活 | 200 | `liveness` · [src/rallymate_service/api.py:487](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:487) |
| GET | [/v1/meta](#route-02) | 发现 API 版本与连接配置 | 200 | `api_meta` · [src/rallymate_service/api.py:491](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:491) |
| GET | [/v1/techniques](#route-03) | 读取技术动作目录 | 200 | `technique_capabilities` · [src/rallymate_service/api.py:539](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:539) |
| GET | [/health/ready](#route-04) | 检查服务处理条件 | 200 | `readiness` · [src/rallymate_service/api.py:554](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:554) |
| GET | [/](#route-05) | 说明 API 端口用途 | 200 | `api_root` · [src/rallymate_service/api.py:663](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:663) |
| GET | [/v1/model-capabilities](#route-06) | 读取模型与评分能力说明 | 200 | `model_capabilities` · [src/rallymate_service/api.py:673](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:673) |
| GET | [/v1/jobs](#route-07) | 列出最近任务 | 200 | `list_jobs` · [src/rallymate_service/api.py:756](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:756) |
| POST | [/v1/jobs](#route-08) | 一次上传视频并创建任务 | 202 | `submit_job` · [src/rallymate_service/api.py:960](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:960) |
| POST | [/v1/uploads](#route-09) | 创建或恢复上传会话 | 201 | `create_upload` · [src/rallymate_service/api.py:984](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:984) |
| GET | [/v1/uploads/{upload_id}](#route-10) | 查询已收到的分片 | 200 | `upload_status` · [src/rallymate_service/api.py:1016](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1016) |
| PUT | [/v1/uploads/{upload_id}/chunks/{index}](#route-11) | 提交一块视频字节 | 200 | `upload_chunk` · [src/rallymate_service/api.py:1020](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1020) |
| POST | [/v1/uploads/{upload_id}/complete](#route-12) | 合并分片并创建任务 | 202 | `complete_upload` · [src/rallymate_service/api.py:1031](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1031) |
| GET | [/v1/jobs/{job_id}](#route-13) | 查询任务状态与结果入口 | 200 | `get_job` · [src/rallymate_service/api.py:1044](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1044) |
| GET | [/v1/jobs/{job_id}/technical-review](#route-14) | 读取人工复核上下文与意见 | 200 | `get_technical_review` · [src/rallymate_service/api.py:1064](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1064) |
| POST | [/v1/jobs/{job_id}/technical-review](#route-15) | 提交或修订人工复核 | 200 | `save_technical_review` · [src/rallymate_service/api.py:1072](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1072) |
| GET | [/v1/jobs/{job_id}/technical-review/export](#route-16) | 导出复核记录与候选标签 | 200 | `export_technical_review` · [src/rallymate_service/api.py:1088](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1088) |
| GET | [/v1/jobs/{job_id}/demo-result](#route-17) | 构建前端综合分析报告 | 200 | `get_demo_result` · [src/rallymate_service/api.py:1099](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1099) |
| GET | [/v1/jobs/{job_id}/trajectory](#route-18) | 读取球与球拍轨迹预览 | 200 | `get_trajectory` · [src/rallymate_service/api.py:1161](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1161) |
| GET | [/v1/jobs/{job_id}/pose-preview](#route-19) | 读取骨架回放窗口 | 200 | `get_pose_preview` · [src/rallymate_service/api.py:1237](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1237) |
| GET | [/v1/jobs/{job_id}/technique-assessment](#route-20) | 评估各技术动作的证据 | 200 | `get_technique_assessment` · [src/rallymate_service/api.py:1280](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1280) |
| GET | [/v1/jobs/{job_id}/artifacts/{artifact_name}](#route-21) | 预览或下载分析产物 | 200 | `get_artifact` · [src/rallymate_service/api.py:1331](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1331) |

当前源码没有在这个应用里挂载 `/assets` 静态目录，没有注册任务 DELETE、取消任务、人工重试任务或修改任务参数接口。框架文档入口 `/docs`、`/redoc`、`/openapi.json`（以及 Swagger UI 的 OAuth2 回调辅助页）不计入上述 21 个显式路由；它们由 FastAPI 默认配置提供。`GET /` 明确使用 `include_in_schema=False`，所以自动 OpenAPI 里应有 20 个显式操作。

自动文档能展示函数签名推导出的请求参数，但项目的返回值大多只标注 `dict`，上传创建体也是通用 `dict`，不能指望 Swagger 自动给出本文这些完整的嵌套字段和业务约束。这里的 21 个计数以 Python AST 提取的装饰器为准；没有通过启动服务来生成本次 OpenAPI 快照。

## 基础状态与能力发现接口

<a id="route-01"></a>

### `GET /health/live` 检查 API 进程存活

源码：[src/rallymate_service/api.py:486](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:486)，处理函数 `liveness()`。

**用途**：进程存活探测。部署系统可以据此判断 HTTP 服务是否能响应。它不检测 GPU、模型、队列处理速度或分析结果是否正确。

- 鉴权：没有 `Depends(authorize)`，无需 Bearer token，即使配置了密钥也是如此。
- 参数：无路径参数、查询参数或请求体。
- 正常响应：HTTP **200**，JSON 固定为 `{"status":"ok"}`。
- 显式业务错误：无。
- 调用及副作用：直接返回字典，不访问数据库，不加载模型，不创建任务。

前端一般无需轮询它展示分析进度；进度应查具体任务。HTTP 服务存活和任务处理正常是两个不同问题。

<a id="route-02"></a>

### `GET /v1/meta` 发现 API 版本与连接配置

源码：[src/rallymate_service/api.py:490](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:490)，处理函数 `api_meta()`。

**用途**：前端初始化时读取服务版本、鉴权方式、端点模板与能力开关，避免把这些信息全部写死在页面中。

鉴权：调用 `authorize`。如果配置了 `api_key`，需要正确 Bearer token；没有密钥时属于允许访问的发现接口。参数：无。

正常返回 HTTP **200**。主要字段如下：

| 字段 | 类型 / 当前构造方式 | 作用 |
|---|---|---|
| `service` | string，`RallyMate Vision API` | 服务名称 |
| `api_version` | string，当前代码常量 `1.3.0` | 对外宣告的 API 功能版本 |
| `compatibility_version` | string，`1.2.0` | 当前保留的兼容版本，OpenAPI 的 `info.version` 也使用它 |
| `environment` | string，服务配置值 | development、staging、production 或 test |
| `public_base_url` | string / null | 公开访问根地址；未配置为 null |
| `authentication.scheme` | `bearer` / `none` | 根据是否配置 API key 决定 |
| `authentication.required` | boolean | 表示是否配置了 API key；不等同于所有业务接口当前可匿名使用 |
| `authentication.browser_secret_injection` | string，`reverse_proxy_or_same_origin_session` | 给前端部署集成的提示，本接口自身不创建登录会话 |
| `capabilities.async_video_jobs` | true | 支持异步视频任务 |
| `capabilities.resumable_uploads` | object | `chunk_bytes=4194304`（4 MiB，来自 `CHUNK_BYTES`）；`parallel_chunks=2`；`expires_after_hours=24` |
| `capabilities.trajectory_preview` | true | 有轨迹预览接口，不保证每段视频都有有效轨迹 |
| `capabilities.racket_bbox_observability` | true | 有球拍框观测能力，不代表球拍精细关键点已经实现 |
| `capabilities.technique_registry` | boolean | 本次能否读取并校验技术目录 |
| `capabilities.formal_coach_score` | false | 此发现文档明确不宣称提供正式教练评分 |
| `technique_registry` | object | 可用时含 `available=true`、`registry_version`、`registry_sha256`；失败时含 `available=false`、`error` |
| `client_configuration` | object | 下表列出的 API 相对路径 / 模板 |

`client_configuration` 中各键的值：

| 键 | 路径 |
|---|---|
| `upload_endpoint` | `/v1/jobs` |
| `job_endpoint_template` | `/v1/jobs/{job_id}` |
| `result_endpoint_template` | `/v1/jobs/{job_id}/demo-result` |
| `trajectory_endpoint_template` | `/v1/jobs/{job_id}/trajectory` |
| `technique_assessment_endpoint_template` | `/v1/jobs/{job_id}/technique-assessment` |
| `technique_catalog_endpoint` | `/v1/techniques` |

内部流程：读取服务配置 → 调用 `technique_catalog()` → 捕获目录异常并写进 `technique_registry.error` → 返回发现文档。**目录不可用不会使这个接口返回 503，它仍返回 200，让前端知道具体哪项能力不可用。** 未通过鉴权则为 401。

前端建议：启动时获取一次，按能力显示功能入口。没有密钥的生产 / 公网配置下，`authentication.required=false` 只描述配置现状；任务接口仍会返回 `503 authentication_not_configured`，不能据此认为整个服务开放匿名访问。

<a id="route-03"></a>

### `GET /v1/techniques` 读取技术动作目录

源码：[src/rallymate_service/api.py:538](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:538)；处理函数 `technique_capabilities()`；实际组装函数 [src/rallymate_scoring/technique_registry.py:269](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_registry.py:269)。

**用途**：提供可识别 / 可评估动作的名称、分类、阶段、证据要求和适用限制，供前端构建动作目录、提示说明及评估页面。它返回规则定义，不返回某个视频的分析结果。

鉴权：与 `/v1/meta` 相同。参数：无。正常响应 HTTP **200**。

| 返回字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` | string | 目录结构版本，当前校验器接受 `1.0.0` |
| `registry_id` | string | 目录标识 |
| `registry_version` | string | 规则内容版本 |
| `registry_sha256` | string | 本次实际读到的目录原始字节的 SHA-256，可用于识别版本变化 |
| `source_documents` | array<object> | 来源文档；每项包含 `file_name`、`scope`、`phase_contract`、`source_sha256` |
| `semantics` | object | 指标语义、评分边界和缺失证据的处理原则 |
| `default_phase_contracts` | object<string,array<string>> | 各动作族的默认阶段顺序 |
| `techniques` | array<object> | 技术动作条目，结构见下表 |

每个 `techniques[]` 的主要字段：

| 字段 | 类型 | 作用 |
|---|---|---|
| `id` | string | 稳定动作 ID，例如源码自带目录中的 `baseline_forehand` |
| `family` | string | 动作族，例如 baseline、serve、return、net_attack、footwork |
| `name_zh` | string | 中文展示名 |
| `aliases` | array<string>，目录可带 | 别名，用于展示或搜索；不是校验器要求的必填字段 |
| `phases` | array<string> | 当前动作的阶段 |
| `core_visual_features` | array<string> | 理论上需要观察的视觉特征 |
| `required_evidence` | array<string> | 必要证据，例如 pose、tracking |
| `enhanced_evidence` | array<string> | 增强证据，可以为空，例如 ball、racket、court |
| `reference_constraints` | array<object>，可选 | 参考约束；每项含 `id/kind/unit/source/semantics`，可含有限数值或 null 的 `value` |
| `proxy_limits` | array<string> | 代理观测不能证明什么，例如没有教练标定时不输出技术等级 |

当前仓库自带的 `src/rallymate_scoring/data/technique_metrics.json` 是 `rallymate-technique-metrics`、版本 `2026-09-12.1`，共 24 项：baseline 5、serve 1、return 5、net_attack 3、footwork 10。**这是本次读取的源码资源，不是线上环境观测结果**；配置 `RALLYMATE_TECHNIQUE_REGISTRY` 后可能读取运营方指定的其他合法目录，前端应按返回内容遍历。

当前 `semantics` 中的 `score_kind=evidence_readiness_reference`、`score_range=[0,100]` 描述证据就绪参考值；`formal_coach_score=false`，`requires_calibration_for_technique_grade=true`。不能把 0–100 范围直接解释成网球水平分。

内部流程：选择配置目录或包内资源 → 读取同一份字节 → 校验 JSON、必需字段、重复 ID、来源哈希及评分语义 → 计算哈希 → 返回目录。没有模型推理，也不写任务。

明确错误：目录读取或校验失败会抛出 `TechniqueRegistryError`，接口转换成 **503**：

```json
{"detail":"technique registry unavailable: 具体原因"}
```

公网 / 生产配置会脱敏错误中的本地路径。此前讨论的自定义异常类就在这里发挥作用：它将多种“目录不可信”原因归为同一类，HTTP 状态与前端响应由 `except` 决定。

<a id="route-04"></a>

### `GET /health/ready` 检查服务处理条件

源码：[src/rallymate_service/api.py:553](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:553)，处理函数 `readiness()`。

**用途**：部署启动检查与故障定位。它比 liveness 深入，但没有运行 GPU 推理，也没有检测 worker 心跳，所以 `ready` 不等于已完成一次模型验证。

鉴权：调用 `authorize`；配置了 API key 就需要 Bearer token；未配置 key 时允许读取诊断。参数：无。

检查内容：

1. 生产环境的 API key 是否存在、至少 32 字符、没有明显占位符内容。
2. `validate_license()` 是否通过：包括环境、模型许可声明、姿态配置、配置路径边界、注册表权威来源等，不仅是许可证名字。
3. 尝试初始化 SQLite 表，并解析评分注册表生命周期权威信息。
4. detect / pose 模型路径是否存在；配置了 `pose_config` 时检查该文件是否存在。
5. 已配置的评分标定资产是否存在；有标定资产时检查可信提升账本、运行时配置绑定、视角证据目录。
6. 在前面没有错误且配置了标定资产时，进一步读取并验证标定资产与绑定注册表。

**响应状态约定：函数正常返回时，无论 `ready` 还是 `not_ready`，HTTP 都是 200。** 调用方必须读取 JSON 中的 `status`，不能只检查 HTTP 码。

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | `ready` / `not_ready` | `reasons` 为空则 ready |
| `reasons` | array<string> | 检查失败的原因；这里始终对本地路径脱敏，不只生产环境 |
| `queue` | object<string,integer> | 数据库任务计数，如 queued/running/succeeded/failed；没有出现的状态不会补 0，应使用 `queue.queued ?? 0` |
| `environment` | string | 当前环境配置 |
| `model_license_ack` | string | 当前许可声明配置 |
| `pose_preset` | string / null | 当前姿态预设 |
| `pose_backend` | string | 当前姿态后端 |
| `pose_profile` | string | 当前姿态运行配置 |
| `pose_native_keypoint_format` | string / null | 原生关键点格式配置 |
| `registry_lifecycle` | object / null | 成功解析时含 `authority_version`、`manifest_sha256`、`role`、`artifact_sha256` |

错误：鉴权错误为 401；各类可收集的检查失败进入 `reasons`。不过最后的 `db.count_by_status()` 不在上面的异常捕获中，因此不能承诺数据库完全不可读时也必定收到 200 诊断文档，未处理异常仍可能导致 500。

副作用：会执行幂等的数据库初始化，可创建表 / 索引并进行已有表兼容升级；不创建视频任务、不运行模型。前端可显示“服务暂未就绪”和 `reasons`；若把它用于负载均衡器探针，需要配置 JSON 状态判定。

<a id="route-05"></a>

### `GET /` 说明 API 端口用途

源码：[src/rallymate_service/api.py:662](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:662)，处理函数 `api_root()`。

无鉴权、无参数，HTTP **200**：

```json
{
  "service": "RallyMate Vision API",
  "frontend": "RallyMate 动作识别工作台",
  "message": "Use the web frontend on the configured public port."
}
```

作用是说明这个端口属于 API，提醒使用独立前端。它不重定向、不返回 HTML 工作台，也不创建前端服务。`include_in_schema=False` 只使它不出现在 OpenAPI 接口列表中，不会禁用此路由。没有显式业务错误或业务副作用。

<a id="route-06"></a>

### `GET /v1/model-capabilities` 读取模型与评分能力说明

源码：[src/rallymate_service/api.py:672](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:672)，处理函数 `model_capabilities()`；静态能力构建器 [src/rallymate_scoring/granularity.py:622](C:/Users/Admin/Documents/网球/src/rallymate_scoring/granularity.py:622)。

**用途**：开发调试、能力说明、展示“为什么有些指标只能观测、有些不能评分”。这里没有 `job_id`，所以它不是指定视频的证据覆盖率或准确率报告。

鉴权：常规受保护接口；已配置密钥时必须 Bearer；生产 / 公网且未配置密钥返回 **503** `authentication_not_configured`。参数：无。正常 HTTP **200**。

| 返回字段 | 类型 / 含义 |
|---|---|
| `schema_version` | string，静态能力结构版本 `1.0.0` |
| `registry_version` | 评分卡注册表版本 |
| `indicator_count` | integer，静态评分卡总数；仓库现有测试固定断言自带数据为 298，客户端不要把部署返回值写死 |
| `domains` | object，各 domain 的评分卡数量 |
| `model_granularity` | object，各模型观测粒度类型对应的评分卡数量 |
| `pose_assessment` | object，含 `pose_dependent_indicators`、`explicit_joint_mapping_complete_indicators`、`recommendation`、`reason` |
| `capability_matrix` | array，body_pose / ball / racket / court / events_and_features 的能力说明 |
| `minimum_scoring_loop` | object，运行时评分注册表与标定配置概要 |
| `technique_registry` | object，定性技术目录概要，或不可用状态 |
| `trajectory_preview` | object，轨迹预览的端点与语义 |

`capability_matrix[]` 的通用字段为 `capability`、`current_output`、`granularity`、`affected_indicators`、`next_action`；body_pose 条目额外包含 `pose_only_indicators`。它来自静态卡片分析，文字中的 COCO-17、场地四点变换等属于静态能力描述，不能代替当前任务 `summary.models` 和实际逐帧输出。尤其当前默认 pipeline 固定关闭场地定位，见提交接口说明。

`minimum_scoring_loop` 的字段：

| 字段 | 说明 |
|---|---|
| `registry_path` | 始终 null，不公布服务器路径 |
| `registry_version` | 当前权威运行注册表版本 |
| `registry_lifecycle` | `authority_version`、`manifest_path=null`、`manifest_sha256`、`role`、`lifecycle`、`artifact_sha256` |
| `indicator_count` | 运行注册表内指标数，与顶层全部评分卡数不同 |
| `indicator_ids` | 排序后的指标 ID 列表 |
| `feasibility_levels` | `指标 ID → 可行性等级` 映射 |
| `grade_policy` | `calibration_required_without_coach_ground_truth` |
| `configured_production_calibration_asset_count` | 已配置标定资产个数，不等价于全部已经通过运行时验证 |
| `trusted_promotion_ledger_configured` | 是否配置可信提升账本 |
| `trusted_runtime_profile_bindings_configured` | 是否配置运行时配置绑定 |
| `runtime_view_evidence_dir_configured` | 是否配置视角证据目录 |
| `trusted_promotion_ledger_path` / `trusted_runtime_profile_bindings_path` / `runtime_view_evidence_dir` | 均为 null |

`technique_registry` 成功时包含 `registry_id`、`registry_version`、`registry_sha256`、`technique_count`、`families`、`formal_score_available=false`；失败时为 `status="unavailable"`、`error`。

`trajectory_preview` 固定声明 `endpoint=/v1/jobs/{job_id}/trajectory`、`prediction=constant_velocity_extrapolation`、`prediction_status=heuristic_preview`、`racket_geometry=bbox_only`。这些分别意味着短时恒速外推、启发式预览、球拍框级几何。

内部流程：生成静态评分卡能力 → 读取权威运行评分注册表 → 加入配置概要 → 尝试读取技术目录 → 加入轨迹语义。读取评分注册表失败是 **500**，错误对象为 `{"detail":{"code":"scoring_registry_unavailable","message":"..."}}`；读取技术目录失败则保留 HTTP 200 并在 `technique_registry` 中降级说明。它不运行推理、不修改视频结果。

## 视频任务接口与公共任务对象

### 公共任务对象：POST / 列表 / 详情共享的大部分响应结构

源码：[src/rallymate_service/api.py:263](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:263) 的 `_public_job()`；数据库解码：[src/rallymate_service/database.py:101](C:/Users/Admin/Documents/网球/src/rallymate_service/database.py:101)。

接口不会直接把 SQLite 行完整返回。`_public_job` 选出公开字段、生成 API URL、按环境处理部分敏感信息，再应用报告兼容投影。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 任务 ID；普通上传新建 UUID，分块完成时使用上传会话 ID |
| `status` | string | queued / running / succeeded / failed |
| `original_filename` | string | 开发环境为规范化后的文件名；生产 / 公网为 `video-<ID前8位><小写扩展名>` |
| `created_at` / `updated_at` | string | 带 UTC 时区的 ISO 8601 时间；前端显示时可转换为用户时区 |
| `started_at` / `completed_at` | string / null | 首次开始 / 结束时间，尚未发生为 null |
| `attempts` | integer | worker 已领取执行的次数，创建时为 0 |
| `progress` | object | 当前进度，至少可按 `phase` 和 `percent` 使用；运行时可包含 `processed_frames`、`total_frames`、`message` 等 |
| `error` | string / null | 任务失败说明；有内容不代表当前 HTTP 请求失败 |
| `summary` | object / null | 分析流水线总结；阶段、版本、证据情况会影响嵌套字段 |
| `scoring_state` | 任意 JSON / null，只在 succeeded 分支添加 | 来自 `summary.scoring_state`；不要假定所有状态都包含此键 |
| `artifact_urls` | object<string,string> | 只在 succeeded 时列出白名单中实际存在的文件，其余状态为 `{}` |
| `demo_result_url` | string / null | succeeded 时为结果接口 URL，其余为 null |
| `trajectory_url` | string / null | running 或 succeeded 时给出 URL，其余 null |
| `technique_assessment_url` | string / null | succeeded 时给出 URL，其余 null |
| `pose_preview_url` | string / null | running 或 succeeded 时给出 URL，其余 null |

URL 在未设置 `public_base_url` 时是 `/v1/...` 相对路径，设置后由公开根地址拼接。URL 出现只说明接口可尝试调用，运行中预览所需文件尚未生成时仍可能返回 409。下载仍走接口鉴权，URL 自身不是免鉴权签名。

当前完整流水线 summary 的核心内容见 [src/rallymate_vision/pipeline.py:1054](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:1054)：`schema_version`、`job_id`、`status="completed"`、`input`、`models`、`processing`、`quality`、`counts`、`coverage`、`primary_player`、`action_recognition`、`minimum_scoring_loop`、`calculation_readiness`、`measurement_portfolio`、`scoring_cycle_measurement`、`scoring_reference_context`、`scoring_state`、`next_stage`、`runtime`、`artifacts` 等。这里的 summary 内部 `status="completed"` 与任务顶层 `status="succeeded"` 属于不同层次，轮询应使用顶层状态。

生产 / 公网投影会规范化文件名、递归脱敏 summary 里的路径和网络标识、处理字符串 `error`。它不会返回原始数据库中的 `video_path`、`request_path`、`output_dir`、`worker_id`、`lease_expires_at` 字段。注意这是对实际实现的说明，不代表任意嵌套字段都经过同一种转换；例如 `progress` 是直接选取的。

<a id="route-07"></a>

### `GET /v1/jobs` 列出最近任务

源码：[src/rallymate_service/api.py:755](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:755)，处理函数 `list_jobs()`。

用途：历史列表、任务切换、查看近期排队状态。鉴权：常规受保护接口。

| 查询参数 | 类型 | 默认 | 限制 / 作用 |
|---|---|---|---|
| `limit` | integer | 20 | 1–100，返回最近最多多少个任务 |
| `report_contract` | `legacy-v1` / `current` | `legacy-v1` | 选择嵌套报告的兼容表示；非法值返回 422 |

HTTP **200**，无任务时的响应为：

```json
{"items": [], "count": 0}
```

有任务时 `items` 为公共任务对象数组。`count` 是本次返回条数，不是数据库任务总数。内部执行 `db.list_jobs(limit)`，按 `created_at DESC` 排序，再逐个转为公共任务对象。没有页码、offset、游标或状态筛选参数。

错误：无效 query 为 422；鉴权为 401 / 未配置公网鉴权为 503。没有单独的“无任务”错误：返回 `{"items":[],"count":0}`。

副作用：读取数据库和文件存在性；不创建 / 重试任务。与单任务详情不同，列表不调用 `summary_with_actions`，历史结果可能保持数据库里较早版本的动作分析，详情页则可能补算。前端应拿 `id` 查详情，不把列表里的 summary 当作永远相同的完整报告。

<a id="route-08"></a>

### `POST /v1/jobs` 一次上传视频并创建任务

源码：[src/rallymate_service/api.py:959](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:959)，处理函数 `submit_job()`；共用处理函数 [src/rallymate_service/api.py:780](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:780) 的 `_submit_video()`。

**用途**：将视频和分析选项提交到后端，生成持久化任务。返回 **202 Accepted** 表示已接收并排队，分析结果需要之后获取。

鉴权：常规受保护接口。请求体：**`multipart/form-data`**。`video` 是文件部分，其余是表单字段，不能把整套参数直接当 JSON 发送。

| 表单字段 | Python 类型 | 必填 / 默认 | 校验与作用 |
|---|---|---|---|
| `video` | `UploadFile` | 必填，`File(...)` | 视频文件；按文件名扩展名允许 `.mp4/.mov/.m4v/.avi/.mkv`，大小写归一化；落盘后还会检查 OpenCV 能否打开、宽高/FPS/帧数是否有效 |
| `court_mode` | string | `disabled` | 仅接受 auto / manual / disabled；当前保存为请求配置，但默认流水线固定关闭场地定位，见下文 |
| `manual_polygon_normalized` | string / null | null | JSON 字符串，解析后必须是 4 个 `[x,y]` 数字坐标，每维 0–1；manual 模式必须提供 |
| `manual_polygon_role` | string | `court_outer_doubles_corners` | 仅接受 `visible_region` / `court_outer_doubles_corners`；用于标明区域语义 |
| `write_annotated_video` | boolean | true | 是否请求写可视化标注视频；用表单值 `true` / `false` |
| `max_players` | integer | 2 | 1–4，传到姿态估计阶段的人数上限；不是限制检测画面里只允许这么多人 |
| `start_ms` | integer | 0 | 分析起始毫秒，必须 ≥ 0 |
| `end_ms` | integer / null | null | 分析结束毫秒；提供时必须大于 start_ms |
| `max_frames` | integer / null | null | 分析帧数上限；提供时必须 ≥ 1 |

`Request` 和鉴权依赖 `_` 由 FastAPI 注入，不是上传字段；服务内部的 `_submit_video(job_id=...)` 也不是本路由开放给前端的参数。普通提交由服务器生成 ID。

**当前场地参数的实际边界**：API 的确校验并保存 auto / manual / polygon 参数；但 [src/rallymate_vision/pipeline.py:690](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:690) 明确在逐帧处理中固定调用 `_disabled_court_observation()`，summary 的 `court_detector` 和 `court_refresh_policy` 也为 disabled。因而当前默认 worker 执行路径中，即使提交 `court_mode=manual`，也不会因此获得场地定位或米制指标。文档保留这些字段的传输约定，前端不要宣称切换此参数已经启用了场地标定。

坐标字段的传输示意：

```text
manual_polygon_normalized = [[0.10,0.20],[0.90,0.20],[0.90,0.90],[0.10,0.90]]
```

这是一个表单字符串，后端执行 `json.loads()` 得到二维数组。当前 API 只检查点数量、维度、数值范围；没有在此处验证四边形面积、角点顺序或是否真是网球场。空字符串 / 仅空格被视为未提供，因此 manual 模式仍会报必填错误。

文件限制来自服务配置，而非客户端参数：`RALLYMATE_MAX_UPLOAD_BYTES` 默认 **2 GiB（2147483648 字节）**；`RALLYMATE_MAX_VIDEO_DURATION_SECONDS` 默认 **1800 秒（30 分钟）**。部署可以覆盖，不能当作线上实测限制。时长检查针对上传源视频的总时长，即使设置了较短 start/end 也不会放宽源文件总时长上限。该接口没有把上述两个限制通过 `/v1/meta` 完整暴露。

内部流程分解：

1. `submit_job` 接收 FastAPI 已解析的文件 / 表单，转交 `_submit_video`。
2. 校验模式、角色、人数、时间范围与帧数。
3. 读取可信评分注册表，以及当前技术目录的版本与哈希。后者随任务写入上游元信息，用来发现排队后目录版本被替换的情况；请求文件中保存的是目录身份信息，并非整份目录正文。
4. 解析多边形；规范化文件名并检查扩展名。原始中文 / 空白等不在允许字符集中的 stem 会被替换，必要时退回 `upload`。
5. 生成 UUID，在 uploads 目录以排他创建方式打开 `<job_id><suffix>`，每次读取最多 1 MiB 并累计检查大小。
6. 拒绝空文件；调用 `probe_video()` 检查 OpenCV 能否打开和视频元信息；按 `frame_count / fps` 估计总时长，拒绝超限视频。这一步没有逐帧完整解码，不能保证后续推理绝不会遇到损坏帧。
7. 写入请求 JSON，内容含源文件、产物目录、服务配置中的模型与设备、分析选项、注册表引用及上游元信息。
8. `db.create_job()` 插入 SQLite，初始 `status=queued`；返回公共任务对象。

请求 JSON 的非前端可配置项包含：模型路径 / device / pose backend，检测图片尺寸 960、姿态图片尺寸 640、检测置信阈值 0.15、姿态置信阈值 0.25、`frame_stride=1`、`court_every_n_frames=25`、服务端评分标定资产列表等。它还记录规范化文件名、content_type、上传字节数、来源 IP 和 request ID。传入 video MIME type 会被记录，但视频是否受支持并不只依靠这个字符串判断。

**202 响应示意，非实际上传观测：**

```json
{
  "id": "11111111-2222-4333-8444-555555555555",
  "status": "queued",
  "original_filename": "tennis.mp4",
  "created_at": "2026-10-09T02:00:00+00:00",
  "updated_at": "2026-10-09T02:00:00+00:00",
  "started_at": null,
  "completed_at": null,
  "attempts": 0,
  "progress": {"phase": "queued", "percent": 0},
  "error": null,
  "summary": null,
  "artifact_urls": {},
  "demo_result_url": null,
  "trajectory_url": null,
  "technique_assessment_url": null,
  "pose_preview_url": null
}
```

此示例采用开发环境文件名；生产 / 公网会返回上述匿名化文件名。创建后 worker 可能很快领取，所以客户端应按实际返回状态处理。

显式错误分支：

| HTTP | 触发条件 | detail 形式 / 关键内容 |
|---|---|---|
| 401 | Bearer 缺失 / 不符 | `missing or invalid bearer token` |
| 503 | 生产 / 公网没有配置 API key | 对象，`code=authentication_not_configured` |
| 422 | 缺少 video、表单不能转成声明类型 | FastAPI 默认校验错误列表 |
| 422 | court_mode 不在集合 | `court_mode must be auto, manual or disabled` |
| 422 | role 不在集合 | `invalid manual_polygon_role` |
| 422 | max_players 超范围 | `max_players must be in the 1..4 range` |
| 422 | start_ms < 0 | `start_ms must be >= 0` |
| 422 | end_ms ≤ start_ms | `end_ms must be greater than start_ms` |
| 422 | max_frames < 1 | `max_frames must be >= 1` |
| 422 | 坐标不是合法 JSON | `manual_polygon_normalized is invalid JSON` |
| 422 | 坐标形状 / 范围错误 | `manual_polygon_normalized must be four [x,y] points in the 0..1 range` |
| 422 | manual 模式没有坐标 | `manual_polygon_normalized is required for manual court mode` |
| 415 | 文件扩展名不受支持 | `unsupported video extension: ...` |
| 413 | 累计字节数超过配置 | `video exceeds the configured size limit` |
| 422 | 文件为空 | `uploaded video is empty` |
| 422 | 视频探测抛出 ValueError / RuntimeError | `video cannot be decoded: ...`，按公开配置脱敏 |
| 413 | 源视频总时长超过配置 | `video exceeds the configured duration limit` |
| 503 | 评分注册表权威信息不可用 | 对象，`code=scoring_registry_unavailable` |
| 503 | 技术目录不可用 | 对象，`code=technique_registry_unavailable` |

这些业务错误按代码次序检查，前面的失败会先返回。请求体解析等框架校验可能更早发生。其他未显式处理的磁盘 / 数据库异常不在上述业务约定内，可能导致 500。

副作用：成功后有视频文件、请求 JSON 和 SQLite 任务记录；不会在 HTTP 处理函数中完成 GPU 推理。写入阶段若失败且尚无数据库任务，会清理对应视频文件；`finally` 关闭上传文件。这里并没有“所有文件与数据库写入都使用同一个事务”的保证。

前端调用示意（通过已经配置好的同源代理，示例省略鉴权注入）：

```javascript
const body = new FormData();
body.append("video", file);
body.append("court_mode", "disabled");
body.append("write_annotated_video", "true");
body.append("max_players", "2");
body.append("start_ms", "0");

const response = await fetch("/v1/jobs", { method: "POST", body });
const payload = await response.json();
if (!response.ok) throw new Error(JSON.stringify(payload.detail));
// 保存 payload.id，后续读取 /v1/jobs/{id}。
```

浏览器会为 FormData 自动生成带 boundary 的 Content-Type，不要把它手工设置成裸 `multipart/form-data`。普通 POST 没有幂等键；网络超时不能直接等同于入队失败，重复提交可能生成另一条任务。较大视频优先采用下面的分块会话协议。

### 异步任务究竟怎样开始执行

源码：[src/rallymate_service/worker.py:197](C:/Users/Admin/Documents/网球/src/rallymate_service/worker.py:197)、[src/rallymate_service/database.py:132](C:/Users/Admin/Documents/网球/src/rallymate_service/database.py:132)。

```text
POST 视频 / 分块完成
    → _submit_video 校验与落盘
    → SQLite: queued
    → 独立 worker 领取任务：running，attempts + 1
    → load_request → run_pipeline / PersistentVisionRunner
    → 进度回调更新数据库并续租
    → 成功：succeeded + summary + completed_at
    → 执行异常：failed + error + completed_at
```

API 文件中的 `async def` 表示 HTTP 处理可使用异步操作；真正的长耗时分析由独立 worker 消费持久化队列，不是因为函数名字是 async 就自动在后台跑完模型。

worker 正常默认以 2 秒空闲间隔检查任务。任务领取租约默认 3600 秒，进度更新会续租，默认最多 2 次领取尝试。需要区分两类失败：执行代码抛异常时 `mark_failed` 直接置为 failed；运行任务租约过期时，在之后 `claim_next` 执行中，未超次数的才重新置 queued，耗尽次数则 failed。这不是“所有失败都会自动重试两次”。

<a id="route-13"></a>

### `GET /v1/jobs/{job_id}` 查询任务状态与结果入口

源码：[src/rallymate_service/api.py:1043](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1043)，处理函数 `get_job()`。

用途：提交后轮询、恢复任务页面、获得分析总结与产物入口。鉴权：常规受保护接口。

| 参数 | 来源 | 类型 / 默认 | 含义 |
|---|---|---|---|
| `job_id` | URL path | string，必填 | 精确查找数据库 ID；此处没有额外 UUID 格式校验 |
| `report_contract` | query | `legacy-v1` / `current`，默认 legacy-v1 | 报告兼容表示选择 |

正常 HTTP **200**，返回一个公共任务对象。即使该任务的 `status=failed`，查询接口本身仍成功，HTTP 仍是 200；前端必须判断顶层任务状态与 error。

内部流程：`db.get_job` → 不存在则 404 → `summary_with_actions(job)` → `_public_job(...)`。历史 succeeded 任务如果缺少当前 detector / motion analysis 版本，`summary_with_actions` 会从已有 `frames.jsonl`、`primary-player.jsonl` 重新派生动作分析，使用至多 12 项的内存缓存，按文件时间 / 大小和版本标识失效。**它不重新运行姿态模型，也不把读时补充的内容回写原始 summary 或数据库。** 因此历史详情显示内容可能比当年磁盘 summary 更丰富。

一个细节：`summary_with_actions` 先执行 `dict(job.get("summary") or {})`；所以尚未有总结的任务在此接口中往往返回 `summary={}`，而提交响应 / 列表可能是 `summary=null`。前端应同时容忍两者。

显式错误：**404** `{"detail":"job not found"}`；无效 report_contract 为 **422**；鉴权为 401 / 503。文件派生处理中的其他未捕获异常仍可能成为 500，不应假定所有历史产物损坏都会被此路由转换为同一种业务错误。

前端建议：queued/running 时适度轮询并用 `progress.phase/percent` 显示状态；succeeded 后读取 demo-result / technique-assessment；failed 后展示 error 并停止正常进度轮询。running 时可以尝试轨迹和姿态预览，但需将“文件尚未准备好”的 409 当作等待状态。`progress.percent` 是后端阶段进度，不是上传网络进度，也不是完成时间估计。

### `report_contract` 为什么在任务查询里出现

来源：[src/rallymate_service/report_compatibility.py:14](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:14) 和 [src/rallymate_service/report_compatibility.py:87](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:87)。

`legacy-v1` 是默认兼容视图，`current` 返回当前原生结构。投影递归检查已知的 `footwork_review` / `motion_analysis` 的 1.1.0 结构，输出旧客户端可理解的字段及 compatibility 来源信息；不会简单把所有 schema_version 都改成 1.0.0。

尤其部分新版模型分类 episode 不支持旧规则表示时，legacy 视图会省略并记录数量，不能把它误读为原始分析从未产生该 episode。开发新客户端或研究完整报告时显式使用 `?report_contract=current`；维护现有旧客户端时沿用默认兼容结构。投影只处理 API 返回表示，不修改不可变产物或缓存中的原生数据。

### 本组接口的现有验证依据

本说明依据源码静态阅读及以下现有测试，不代表本次已启动服务或上传真实视频：[tests/test_service.py:48](C:/Users/Admin/Documents/网球/tests/test_service.py:48) 覆盖上传→排队→模拟 worker→查询、能力数字与列表；[tests/test_service.py:370](C:/Users/Admin/Documents/网球/tests/test_service.py:370) 覆盖 Bearer；[tests/test_service_boundaries.py:74](C:/Users/Admin/Documents/网球/tests/test_service_boundaries.py:74) 覆盖生产未配置密钥仍返回 200/not_ready；[tests/test_service_boundaries.py:118](C:/Users/Admin/Documents/网球/tests/test_service_boundaries.py:118) 覆盖 readiness 路径脱敏；[tests/test_motion_api.py:17](C:/Users/Admin/Documents/网球/tests/test_motion_api.py:17) 覆盖历史 summary 读时补动作且不回写；[tests/test_report_compatibility.py:98](C:/Users/Admin/Documents/网球/tests/test_report_compatibility.py:98) 附近覆盖默认兼容视图、current 视图与非法 query。

## 分片上传：先可靠传文件，再创建分析任务

这组接口解决的是大视频和不稳定网络下的传输问题。客户端把原视频按字节切块，后端保存每块及其 SHA-256；中断后查询已收到的块，只补传缺少的块。所有块合并成原始完整文件后，才进入与 `POST /v1/jobs` 相同的提交逻辑。**一块数据不是一段可独立分析的视频；上传分块时不会运行推理，也不会创建分析任务。**

源码入口：[src/rallymate_service/api.py:981](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:981)；持久化与重传实现：[src/rallymate_service/uploads.py:46](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:46)；串联流程测试：[tests/test_resumable_uploads.py:165](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:165)。

### 四个接口与调用顺序

| 顺序 | 方法与路径 | 功能 | 正常 HTTP 状态 |
|---|---|---|---|
| 1 | `POST /v1/uploads` | 使用客户端 UUID 创建或恢复上传会话，固定文件信息和分析选项 | `201` |
| 2 | `GET /v1/uploads/{upload_id}` | 获取断点、分块大小和已有任务 ID | `200` |
| 3 | `PUT /v1/uploads/{upload_id}/chunks/{index}` | 写入一个分块；可乱序、可安全重传相同内容 | `200` |
| 4 | `POST /v1/uploads/{upload_id}/complete` | 检查齐全、重组完整视频、校验可解码性并入队 | `202` |

四个接口都声明了 `Depends(authorize)`。配置了 API Key 时请求必须包含 `Authorization: Bearer <实际令牌>`；本地开发且未配置 Key 时允许访问；生产环境或配置了公共 URL 却没有 Key 时会拒绝。鉴权实现见 [src/rallymate_service/api.py:458](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:458)。

| 共用鉴权失败 | 返回内容 |
|---|---|
| `401` | `{"detail":"missing or invalid bearer token"}`，附带 `WWW-Authenticate: Bearer` |
| `503` | `detail` 为对象，`code="authentication_not_configured"`，`message="RALLYMATE_API_KEY is required for public API access"` |

以下错误表列出应用明确实现的业务错误。缺少请求体、请求体不是对象、路径参数无法转成整数等框架校验错误通常返回 `422`，`detail` 为校验条目列表。

### 共用上传会话响应

创建、查询、上传单块都返回同一类对象；`complete` 返回的是任务对象。源码：[src/rallymate_service/uploads.py:78](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:78)。

| 字段 | JSON 类型 | 含义 |
|---|---|---|
| `upload_id` | string | 客户端提供并被服务器接受的标准 UUID |
| `size` | integer | 完整原始视频总字节数 |
| `chunk_size` | integer | 此会话的切块单位，客户端必须以此切片 |
| `chunk_count` | integer | 总块数，等于向上取整 `size / chunk_size` |
| `received_chunks` | integer[] | 已持久化分块的序号，升序排列，序号从 `0` 开始 |
| `chunk_sha256` | object | 已收分块的 SHA-256，键为字符串序号，例如 `{"0":"…"}` |
| `received_bytes` | integer | 已收块的字节数总和；不是视频分析进度 |
| `job_id` | string 或 null | 成功提交后等于 `upload_id`；尚未提交则为 `null` |
| `expires_in_seconds` | integer | 固定返回 `86400`，表示 TTL 配置，**不是实时剩余秒数** |

示例（仅展示结构）：

```json
{
  "upload_id": "3d432d94-fca6-4cda-a608-764f42454133",
  "size": 262144,
  "chunk_size": 131072,
  "chunk_count": 2,
  "received_chunks": [],
  "chunk_sha256": {},
  "received_bytes": 0,
  "job_id": null,
  "expires_in_seconds": 86400
}
```

**分块大小有两个不同概念。** 当前新会话采用 `128 * 1024 = 131072` 字节，即 128 KiB。单次请求的硬上限仍然是 `4 * 1024 * 1024 = 4194304` 字节，即 4 MiB，用于兼容历史会话；历史会话可能仍以 4 MiB 切块。`/v1/meta` 中的 `resumable_uploads.chunk_bytes` 返回后者，所以不能拿它代替会话的 `chunk_size`。定义见 [src/rallymate_service/uploads.py:16](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:16)，元信息见 [src/rallymate_service/api.py:519](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:519)。

<a id="route-09"></a>

### `POST /v1/uploads` 创建或恢复上传会话

接口定义：[src/rallymate_service/api.py:983](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:983)。存储逻辑：[src/rallymate_service/uploads.py:89](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:89)。

请求为 `application/json`，主体是 JSON 对象，对应函数参数 `payload: dict`。它不是表单，也不接收视频二进制。

| 请求字段 | 类型 | 必填/默认 | 限制和说明 |
|---|---|---|---|
| `upload_id` | string | 必须提供才能成功 | 标准小写带连字符 UUID，例如 `crypto.randomUUID()`；服务器不会代为生成。校验要求 `str(uuid.UUID(value)) == value`，因此大写或无连字符写法也不接受 |
| `filename` | string | 建议始终提供；缺失或空串实际回退为 `upload.mp4` | 清理为安全文件名后检查后缀；允许 `.mp4`、`.mov`、`.m4v`、`.avi`、`.mkv`。扩展名忽略大小写。合并完整文件后的打开、元信息与总时长检查在 complete 阶段进行，不是逐帧完整解码 |
| `size` | integer | 必须提供才能成功 | `0 < size <= max_upload_bytes`；拒绝 bool、字符串、浮点数。默认上限为 2 GiB，即 `2147483648` 字节，可由 `RALLYMATE_MAX_UPLOAD_BYTES` 修改 |
| `options` | object | 默认 `{}` | 只接受下面八种键；`null`、数组和未知键会被拒绝 |

这里的“必须”指业务逻辑上的必要条件：入口使用普通 `dict`，没有针对这些字段定义 Pydantic 请求模型。缺少 `upload_id` 可能最终是 `404`，缺少 `size` 则是 `413`，不全是框架的 `422`。外层多余字段目前会被忽略，`options` 中的多余字段会明确拒绝。`filename` 当前先经 `str(...)` 转换，没有严格的字符串类型校验；前端仍应按表中约定发送字符串。相关代码见 [src/rallymate_service/api.py:984](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:984)。

`options` 中字段的默认值来自最终 `_submit_video` 函数；这些默认值不会自动补写进会话的原始 `options` 字典。

| `options` 字段 | 类型 | 默认 | 校验与作用 |
|---|---|---|---|
| `court_mode` | string | `"disabled"` | `"auto"`、`"manual"`、`"disabled"` 三选一；校验并保存请求的场地模式，但当前默认流水线固定关闭场地定位，见一次性上传章节 |
| `manual_polygon_normalized` | string 或 null | `null` | 注意是 JSON **字符串**，例如 `"[[0.1,0.2],[0.9,0.2],[0.9,0.9],[0.1,0.9]]"`，不是直接传数组；内容必须是四个 `[x,y]` 点，坐标在 `0..1`；manual 模式要求有有效值 |
| `manual_polygon_role` | string | `"court_outer_doubles_corners"` | `"visible_region"` 或 `"court_outer_doubles_corners"`，声明标注点的语义 |
| `write_annotated_video` | boolean | `true` | 是否输出带标注视频；必须是 JSON `true/false`，不接受字符串 `"true"` |
| `max_players` | integer | `2` | `1..4`，不能为 `null`，不能用 bool 代替整数 |
| `start_ms` | integer | `0` | 分析起始毫秒，`>=0`，不能为 `null` |
| `end_ms` | integer 或 null | `null` | 指定时必须 `> start_ms` |
| `max_frames` | integer 或 null | `null` | 指定时必须 `>=1`，限制处理帧数 |

`_parse_polygon` 只在这里检查点数、二维结构及数值范围；这不等于已经证明四个点构成正确球场。代码也没有在创建会话时检查起止时间是否落在视频实际时长内。源码：[src/rallymate_service/api.py:339](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:339)、[src/rallymate_service/api.py:992](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:992)。

请求示例：

```http
POST /v1/uploads
Authorization: Bearer <实际令牌>
Content-Type: application/json

{
  "upload_id": "3d432d94-fca6-4cda-a608-764f42454133",
  "filename": "practice.mp4",
  "size": 262144,
  "options": {
    "court_mode": "disabled",
    "write_annotated_video": true,
    "max_players": 2,
    "start_ms": 0,
    "end_ms": null,
    "max_frames": null
  }
}
```

成功返回 `201` 和上传会话对象。重复发送同一 `upload_id`、同一清理后文件名、同一大小、同一 `options` 字典时返回原会话；HTTP 状态仍为 `201`。它不会清空已经收到的分块。

**重试必须保留原始选项写法。** 第一次传 `{}`，第二次传 `{"court_mode":"disabled"}`，虽然运行效果相同，但字典不同，会返回 `409`。这一比较用于避免把旧文件的字节接到另一份配置上；它并不计算整份客户端文件的指纹，因此客户端恢复时仍应检查文件身份。

| HTTP 状态 | `detail` 或触发条件 |
|---|---|
| `404` | UUID 不符合标准：`上传会话不存在` |
| `409` | 相同 ID 的文件名、大小或原始选项不同：`上传会话与所选文件不一致` |
| `410` | 会话过期：`上传会话已过期，请重新开始上传` |
| `410` | 尚未收到任何块、尚未提交的历史大块会话：`上传分块方式已更新，请重新建立上传会话`；应换新 UUID 建立会话 |
| `413` | `视频为空或超过上传大小限制`，也用于 `size` 类型不正确 |
| `415` | `不支持的视频格式` |
| `422` | `无效的分析选项`、`分析参数必须为整数`、`write_annotated_video必须为布尔值`、`分析参数格式不正确`、`无效的场地模式`、`无效的场地标定类型`、`无效的人数或起始时间`、`无效的视频分析范围`、`手工场地模式需要标定点`，按失败的检查分别返回 |
| `422` | 标注字符串不能解析：`manual_polygon_normalized is invalid JSON`；结构不符合：`manual_polygon_normalized must be four [x,y] points in the 0..1 range` |
| `429` | 未提交且未过期的活跃会话达到 64 个：`上传人数较多，请稍后继续` |
| `507` | 可用磁盘不足：`云端可用空间不足，请稍后重试` |

磁盘门槛当前为 `3 * (本次 size + 已有活跃会话预留 size 总和) + 512 MiB`。三倍空间用于容纳分块、合并文件和最终任务输入同时存在的阶段。新会话创建时会清理过期或已提交会话残留的字节文件，同时保留会话清单。源码：[src/rallymate_service/uploads.py:93](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:93)、[src/rallymate_service/uploads.py:107](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:107)、[src/rallymate_service/uploads.py:203](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:203)。

<a id="route-10"></a>

### `GET /v1/uploads/{upload_id}` 查询已收到的分片

接口定义：[src/rallymate_service/api.py:1015](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1015)；存储逻辑：[src/rallymate_service/uploads.py:126](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:126)。

参数只有路径中的 `upload_id`；没有请求体和业务查询参数。成功为 `200`，返回共用会话对象。

前端可以据此显示 `received_bytes / size` 的上传进度，并根据 `received_chunks` 跳过已有块。`chunk_sha256` 可用于检查本地所选文件在对应位置的字节是否与服务器一致。`job_id` 不为空时可以直接去查询任务，不需要再次传输视频。

| HTTP 状态 | `detail` |
|---|---|
| `404` | ID 格式错误：`上传会话不存在`；无清单：`上传会话不存在，请重新开始上传` |
| `410` | `上传会话已过期，请重新开始上传` |

GET 只读取清单，不会延长有效期。会话未提交时，从最近一次真正保存清单的时间起，超过 24 小时会过期；成功写入新块会更新时间。已绑定 `job_id` 的会话不受这个过期判断限制。因此 `expires_in_seconds=86400` 不能被前端直接当作倒计时。源码：[src/rallymate_service/uploads.py:60](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:60)、[src/rallymate_service/uploads.py:69](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:69)。

<a id="route-11"></a>

### `PUT /v1/uploads/{upload_id}/chunks/{index}` 提交一块视频字节

接口定义：[src/rallymate_service/api.py:1019](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1019)；存储逻辑：[src/rallymate_service/uploads.py:130](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:130)。

请求体是该块的**原始二进制字节**。建议 `Content-Type: application/octet-stream`；实现并未强制 MIME 类型，但不能包装成 JSON 或 multipart。后端读取 `request.stream()`，因此即使不提供或伪造 `Content-Length`，仍会按实际读取字节数限制大小。

| 参数 | 位置 | 类型/要求 |
|---|---|---|
| `upload_id` | path | 已存在且未过期的标准 UUID |
| `index` | path | integer，`0 <= index < chunk_count` |
| `X-Chunk-SHA256` | header | 必须提供才能成功；当前块的 SHA-256 十六进制摘要，长度 64；大小写均可，保存时转小写 |
| 原始分块 | body | 长度必须严格等于 `min(chunk_size, size - index * chunk_size)` |

例如 `chunk_size=131072` 时，块 0 取原文件字节区间 `[0, 131072)`，块 1 取 `[131072, 262144)`，最后一块可以短于 `chunk_size`。摘要必须针对这一块的字节计算，不是针对整个视频计算。

成功返回 `200` 和更新后的共用会话对象。允许乱序上传，也可并发上传不同块。写入前后使用跨进程文件锁，并采用临时文件、`fsync` 和原子替换持久化数据及清单；这使服务重启后仍能读到进度。源码：[src/rallymate_service/uploads.py:22](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:22)、[src/rallymate_service/uploads.py:135](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:135)。

重复上传同一序号、同一摘要和正确字节的请求不会重复累计大小；不同内容会返回 `409`。已提交会话收到相同已记录分块时仍可返回原会话；这是由于“相同块重试”的检查早于“已提交禁止写入”的检查。相同块的成功重试不会刷新 `updated_at`。

| HTTP 状态 | `detail` |
|---|---|
| `404` / `410` | 与 GET 的会话不存在/过期错误相同 |
| `413` | 实际请求体超过 4 MiB：`分块过大` |
| `422` | 缺少摘要、长度不是 64 或摘要不匹配：`分块校验失败，请重传此块` |
| `422` | 数值序号越界：`无效的分块序号`；非整数路径值由框架返回校验错误 |
| `422` | 字节长度与此块应有长度不符：`分块长度不正确` |
| `409` | 相同序号已有不同摘要：`同一分块已存在不同内容` |
| `409` | 已绑定任务后试图增加新块：`视频已提交推理` |

<a id="route-12"></a>

### `POST /v1/uploads/{upload_id}/complete` 合并分片并创建任务

接口定义：[src/rallymate_service/api.py:1030](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1030)；合并逻辑：[src/rallymate_service/uploads.py:160](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:160)；共用视频提交逻辑：[src/rallymate_service/api.py:780](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:780)。

只有路径参数 `upload_id`，**不需要请求体**。分析选项使用创建会话时保存的 `options`；此处不能再改选项。

执行过程：

1. 读取会话并加会话锁。
2. 先用 `upload_id` 查询任务库；若已有任务，绑定会话、清理分块并返回现有任务。
3. 确认已收到所有块；按序读取每块，再核对保存大小和 SHA-256。发现损坏时删除该块的清单记录，让客户端能够通过 GET 发现并补传。
4. 写入完整合并文件，验证最终字节数。
5. 调用 `_submit_video(..., job_id=upload_id, **options)`：校验注册表、分析选项和多边形；写入正式输入文件；用 `probe_video` 检查能否解码及总时长；生成 worker 请求文件；在 SQLite 创建 `queued` 任务。
6. 保存 `job_id=upload_id`，删除分块文件；合并临时文件在 `finally` 中删除。

这一步会检查完整视频，但不会在 HTTP 请求内执行整套姿态识别/评分推理；分析任务交给 worker。返回 `202 Accepted` 表示已受理，不表示分析已完成。正常首次创建任务见 [src/rallymate_service/database.py:71](C:/Users/Admin/Documents/网球/src/rallymate_service/database.py:71)。

成功响应为 `_public_job` 对象，格式与 `POST /v1/jobs` 一致。首次成功通常如下；时间和 ID 为示例：

```json
{
  "id": "3d432d94-fca6-4cda-a608-764f42454133",
  "status": "queued",
  "original_filename": "practice.mp4",
  "created_at": "2026-10-09T02:00:00+00:00",
  "updated_at": "2026-10-09T02:00:00+00:00",
  "started_at": null,
  "completed_at": null,
  "attempts": 0,
  "progress": {"phase": "queued", "percent": 0},
  "error": null,
  "summary": null,
  "artifact_urls": {},
  "demo_result_url": null,
  "trajectory_url": null,
  "technique_assessment_url": null,
  "pose_preview_url": null
}
```

公开/生产配置下 `original_filename` 会变成 `video-<ID前8位>.<后缀>` 形式；例如上例为 `video-3d432d94.mp4`。任务已经存在时，重复 complete 返回它的当前状态，可能已经是 `running`、`succeeded` 或 `failed`，并可能带有可访问产物链接和 `scoring_state`。响应依旧为 `202`，不会为了重试创建第二个任务。任务响应映射见 [src/rallymate_service/api.py:263](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:263)。

| HTTP 状态 | 触发条件/`detail` |
|---|---|
| `404` / `410` | 会话不存在或过期，与 GET 相同 |
| `409` | 尚未上传齐：`还有分块未上传，请继续上传后再合并` |
| `409` | 已保存块缺失或损坏：`分块存储校验失败，请继续上传以补齐`；重新 GET，再补传 |
| `422` | `视频合并长度不正确` |
| `422` | 无法解码：`video cannot be decoded: <具体原因>`；公开配置会脱敏本地路径 |
| `413` | 超过配置字节上限：`video exceeds the configured size limit`；超过视频总时长上限：`video exceeds the configured duration limit` |
| `503` | 评分注册表不可用：`detail.code="scoring_registry_unavailable"` |
| `503` | 技术动作注册表不可用：`detail.code="technique_registry_unavailable"` |

此接口也复用 `_submit_video` 的 `court_mode`、标定角色、人数、时间范围、标注 JSON、空视频及文件扩展名检查，因此共享该提交函数的其他 `422/415` 错误。正常未被篡改的新会话已在创建时通过这些选项检查，但合并后仍需校验真实视频和当时的后端注册表。

总视频时长默认上限 `1800` 秒（30 分钟），可通过 `RALLYMATE_MAX_VIDEO_DURATION_SECONDS` 配置。它检查的是原始视频时长，而不是 `end_ms-start_ms` 的分析片段时长。默认字节上限和时长上限见 [src/rallymate_service/config.py:125](C:/Users/Admin/Documents/网球/src/rallymate_service/config.py:125)、环境变量读取见 [src/rallymate_service/config.py:513](C:/Users/Admin/Documents/网球/src/rallymate_service/config.py:513)。

**重试语义与边界：**

- complete 的网络响应丢失后，可以用相同 ID 重试；数据库已存在任务时直接返回现有任务。这也覆盖“任务已经持久化但会话尚未来得及标记”的常见崩溃窗口。
- 任务已失败时，重复 complete 仍然返回这个失败任务，不会重新排队。若需要重新分析，应开始新的上传/任务流程；当前这四个接口没有“重试推理”或“修改分析配置”的操作。
- 因注册表临时不可用等问题尚未入队时，分块通常保留，修复后可重试 complete。若是不可解码视频、已过期会话或需要修改配置，则需要重新选择文件/新建会话。
- 存储实现先检查未绑定任务会话是否过期，才查询已有任务。因此若“入库成功但会话标记未完成”后一直等到超过 24 小时再重试，仍可能先得到 `410`；不能把它解读为不受任何存储/过期条件影响的绝对一次性保证。
- GET 会话本身不会核验磁盘上每一块的当前完整性；合并时才做第二次校验。摘要记录存在，不保证该块在之后未发生磁盘损坏。

### 从前端调用时如何串起来

下面是说明协议的 JavaScript 示例，不会自动执行。`file` 是用户选中的 `File`，`uploadId` 应在首次上传前生成并保存；发生网络中断时保留这个 ID，再选择同一个文件并使用同一组选项调用。`apiFetch` 表示项目中已经处理 API 基址和鉴权的请求函数，接收标准 fetch 参数；这里没有嵌入真实令牌。

```javascript
// 仅示例：apiFetch(path, init) 应返回标准 Fetch Response。
async function resumeUpload(apiFetch, file, uploadId, options = {}) {
  async function jsonRequest(path, init) {
    const response = await apiFetch(path, init);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${JSON.stringify(data.detail)}`);
    }
    return data;
  }

  // 相同 ID + 文件信息 + 原始 options 会恢复会话。
  let session = await jsonRequest("/v1/uploads", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      upload_id: uploadId,
      filename: file.name,
      size: file.size,
      options
    })
  });

  const base = `/v1/uploads/${session.upload_id}`;
  session = await jsonRequest(base, {method: "GET"});
  if (session.job_id) {
    return jsonRequest(`/v1/jobs/${session.job_id}`, {method: "GET"});
  }

  const received = new Set(session.received_chunks);
  for (let index = 0; index < session.chunk_count; index++) {
    // 始终服从会话 chunk_size，不能硬编码 meta 中的 4 MiB。
    const start = index * session.chunk_size;
    const chunk = file.slice(start, Math.min(file.size, start + session.chunk_size));
    const hash = await crypto.subtle.digest("SHA-256", await chunk.arrayBuffer());
    const digest = Array.from(new Uint8Array(hash), b =>
      b.toString(16).padStart(2, "0")).join("");

    if (received.has(index)) {
      if (session.chunk_sha256[String(index)] !== digest) {
        throw new Error("选择的文件与已有上传分块不一致，请用新 ID 开始上传");
      }
      continue;
    }

    await jsonRequest(`${base}/chunks/${index}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Chunk-SHA256": digest
      },
      body: chunk
    });
  }

  // 返回任务，后续轮询 /v1/jobs/{id}。
  return jsonRequest(`${base}/complete`, {method: "POST"});
}

// 初次上传示意：先保存这个 ID，再调用 resumeUpload。
// const uploadId = crypto.randomUUID();
// const job = await resumeUpload(apiFetch, file, uploadId, {court_mode: "disabled"});
```

这是顺序上传的可读示例。生产客户端还可以在明确的失败处理逻辑中加入有限重试、断网提示和并发控制；网络不确定时先查询断点，不要盲目创建新 ID。遇到 `410` 应重建会话；`409` 应根据具体原因决定补传还是换新会话；完整视频无法解码时重传相同字节不会改变文件内容。

### 与一次性上传的区别

| 对比项 | `POST /v1/jobs` | 四步分片上传 |
|---|---|---|
| 请求格式 | 一次 `multipart/form-data`，包含视频及表单选项 | 创建会话使用 JSON，分块使用二进制，complete 不要请求体 |
| ID | 服务器生成任务 UUID | 客户端先生成 upload UUID，最终任务 ID 与它相同 |
| 断点恢复 | 该接口没有分块断点协议 | 查询清单，跳过已有块、补传缺失块 |
| 选项类型 | 表单字符串由 FastAPI 按类型解析 | JSON 整数/布尔值严格校验，不接受数字字符串或布尔字符串 |
| 视频验证 | 上传后立即解码检查并提交 | 创建时检查元信息，complete 时验证完整视频并提交 |
| 重复提交 | 再次 POST 通常创建新的任务 | 同一 upload ID 的 complete 返回已存在的任务 |
| 什么时候推理 | 入队后由 worker 执行 | 所有分块合并并入队后由同一 worker 流程执行 |

### 已有测试覆盖与实现边界

以下是源码中已经存在的测试内容，本节不把静态阅读当作本轮执行通过的测试报告：

- [tests/test_resumable_uploads.py:25](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:25)：乱序接收、进度持久化、摘要错误、相同块并发重传、并发 complete 只调用一次入队。
- [tests/test_resumable_uploads.py:71](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:71)、[tests/test_resumable_uploads.py:93](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:93)、[tests/test_resumable_uploads.py:120](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:120)：旧 4 MiB 会话的更新提醒、部分上传续传、已完成会话保持原协议。
- [tests/test_resumable_uploads.py:134](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:134)：磁盘损坏后从清单删除坏块，允许补传。
- [tests/test_resumable_uploads.py:145](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:145)：身份冲突、非法 ID 和过期清理。
- [tests/test_resumable_uploads.py:165](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:165)：HTTP 鉴权、API 重启后继续、complete 前没有任务、成功后只存在一个任务、合并内容与原视频一致。

当前没有单独的取消/删除上传会话接口，没有针对单个用户的会话归属字段，64 个活跃会话上限是此上传根目录下的总数。传输会话也没有 `status` 字段；前端应从 `received_chunks`、`chunk_count`、`job_id` 和 HTTP 状态判断阶段，不能凭空期待 `uploading/completed` 枚举。

## 分析结果、回放与人工复核

本节记录直接调用 Python 服务的合同。所有这些接口都使用 `Depends(authorize)`：配置 API key 时需要完整的 `Authorization: Bearer <key>`；缺失或不匹配返回 `401`，并有 `WWW-Authenticate: Bearer`。公开部署或 `production` 环境未配置 key 时返回 `503`，`detail.code=authentication_not_configured`；未配置 key 的本地开发环境可匿名访问。这里是共享服务凭据，代码未提供按任务所有者的二次授权，也不核验人工评审者的真实身份。来源：[src/rallymate_service/api.py:458](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:458)。

下文 JSON 示例均为**字段结构示意**，不是运行服务得到的真实结果。`null` 表示没有可用结论，不能自动替换成 0、最低等级或“动作做得差”。HTTP 错误通常由 FastAPI 包成 `{"detail": ...}`；各接口标出的对象是 `detail` 的内容。

### 报告版本与结果语义

`demo-result` 和 `technique-assessment` 的 `report_contract` 是查询参数，枚举 `legacy-v1|current`，默认 **`legacy-v1`**；不支持的值由参数校验返回 `422`。文件下载的同名参数默认却是 **`current`**。`trajectory`、`pose-preview` 和人工复核接口没有该参数。

`legacy-v1` 递归处理报告中已知的两个 `schema_version=1.1.0` 子结构：`footwork_review` 投影为 1.0，去掉新版独立测量明细等字段；`motion_analysis` 投影为 1.0，只保留旧版支持的 `rule_inferred`、`unclassified` 片段。模型分类片段可能因此省略，返回 `compatibility.omitted_unsupported_episode_count`。每个被投影的结构带 `compatibility`，含 `projection_version`、`source_schema_version`、`target_schema_version`、`native_representation_query`、`measurement_values_preserved`。历史 1.0 及未知未来版本不会被冒充成 1.0。投影不改数据库、缓存原对象或原始产物；`current` 返回本机原生结构。来源：[src/rallymate_service/report_compatibility.py:14](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:14)、[src/rallymate_service/report_compatibility.py:34](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:34)、[src/rallymate_service/report_compatibility.py:52](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:52)、[src/rallymate_service/report_compatibility.py:87](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:87)；对应既有测试 [tests/test_report_compatibility.py:80](C:/Users/Admin/Documents/网球/tests/test_report_compatibility.py:80)。

三类输出必须区分：

|输出|含义|不能作出的解释|
|---|---|---|
|机器检测、动作候选、测量与证据参考分|当前视频提供的观测与测量完整度、可复核线索|识别准确率、确认击球次数、正式技术水平|
|人工 `technical-review`|评审者按来源规则对指定人物和时段提交的人工观察或 A～E|系统自动评分、身份认证签名、已批准标定真值|
|正式评分|需要另行通过教练标定与可信授权的产物语义|不能把本节的参考分或人工等级直接充当正式结果|

<a id="route-14"></a>

### `GET /v1/jobs/{job_id}/technical-review` 读取人工复核上下文与意见

用途：取得该任务的人工复核上下文、规则目录和每条评审的最新版本。首次读取可以正常返回 `revision=0`、`entries=[]`，并不会凭空生成评审。定义：[src/rallymate_service/api.py:1063](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1063)；核心实现：[src/rallymate_service/technical_review.py:336](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:336)、[src/rallymate_service/technical_review.py:351](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:351)。

**请求与前提。** 只有路径参数 `job_id`（字符串）；无请求体、无分页或过滤参数。任务必须 `succeeded`；原视频、`primary-player.jsonl`、`events.jsonl` 必须可读且可校验。summary 还须有有效 `input.video.duration_ms`、`fps` 以及 `processing.source_start_frame`、`source_end_frame_exclusive`。可复核时间范围由已分析源帧范围换算并裁剪至视频时长，并不等于必然覆盖整段视频。人物区间来自主球员时间线，事件必须绑定有效人物和分析区间。来源：[src/rallymate_service/technical_review.py:164](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:164)。

**成功响应：`200 application/json`，`Cache-Control: no-store`。**

|字段|类型及含义|
|---|---|
|`schema_version`|固定 `technical-review-v1.0.0`|
|`job_id`、`revision`|任务 ID；所有历史提交的数量，也是下次写入使用的乐观锁版本|
|`source_sha256`|服务端重新计算的原视频 SHA-256|
|`source_reference_version`、`source_reference_sha256`|规则包版本和来源指纹；当前版本要求 `scoring-reference-20261004`|
|`artifact_context_sha256`|绑定视频指纹、分析范围、人物时间线字节和事件字节的上下文指纹|
|`video`|`duration_ms`、`review_start_ms`、`review_end_ms`|
|`players[]`|每项 `player_id`、`start_ms`、`end_ms`，按人物编号排序|
|`events[]`|每项 `event_id`、`event_code`、`player_id`、`start_ms`、`end_ms`|
|`rules[]`|298 项来源指标的公开规则摘要，字段见下文|
|`visual_rules[]`|246 条来源视觉规则；返回规则包中的完整记录，字段见下文|
|`entries[]`|每个 `review_id` 的最新记录，按 `job_revision` 排序；历史版本需调用 export|
|语义字段|见本节下方固定语义字段表|

`rules[]` 每项：`indicator_id`、`event_code`、`manual_grading_allowed`、`blockers`、`warnings`、`source_document_id`、`source_document_sha256`、`source_table`、`rubric_sha256`。注意这个 GET 的指标规则摘要**不返回完整 `grades` 定义、指标名称、定义和计算方式**；保存结果会附上选中等级的 `selected_grade_definition`。`blockers`、`warnings` 内容由来源目录决定。

`visual_rules[]` 当前字段：`visual_rule_id`、`technique_ids[]`、`stage_id`、`phase_id`、`optional_in_source`、`optional_positive_observation`、`source_document_id`、`source_document_sha256`、`source_locator`、`source_text`、`rubric_sha256`。这些是目录驱动的记录，客户端不能假定所有视觉规则都适用于同一技术或阶段。来源：[src/rallymate_service/technical_review.py:129](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:129)、[src/rallymate_service/technical_review.py:151](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:151)；[src/rallymate_scoring/data/technical_review_rules.json:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/data/technical_review_rules.json:1)。

每个 `entries[]` 记录保存了 POST 的业务字段（不包括 `expected_revision`），另外包含：

- 身份和审计：`schema_version`、`job_id`、`review_id`、`job_revision`、`entry_revision`、`created_at`、`saved_at`、`previous_record_sha256`、`record_sha256`。时间为 UTC ISO 字符串；新增时 `entry_revision=1`、前序哈希为 null。
- 来源补充：`event_code`、`source_document_id`、`source_document_sha256`、`source_table`、`source_locator`、`stage_id`、`optional_in_source`、`source_text`、`rubric_sha256`、`selected_grade_definition`、`source_blockers`、`source_warnings`。指标和视觉规则字段不完全相同，不适用字段可能为 null。
- `source_binding_current`：当前视频、规则版本/指纹和上下文指纹是否仍与该条记录相符；旧记录不会因为来源变化自动改写。
- 下列固定语义字段也出现在顶层和记录内。

|固定字段|值|
|---|---|
|`semantics`|`human_source_rule_review`|
|`formal_grade`、`formal_score`|`null`|
|`calibration_eligible`、`production_scoring_enabled`|`false`|
|`calibration_status`|`requires_independent_adjudication_and_split`|
|`identity_verification`|`self_reported_local`|
|`observability_source`|`manual_visual_review`|
|`automatic_joint_gate_passed`|`null`|

**错误。** `404`：任务不存在；`409`：`job_not_completed`、`review_context_unavailable`、`invalid_review_artifact`、`review_artifacts_changed`、`review_history_invalid`；`503`：`source_reference_unavailable`。业务错误内容为 `{code,message}`，消息不会回显任意请求文本。除此之外适用共享鉴权错误。

**读取与副作用。** 上下文缓存未命中时会读取并哈希整个原视频，读取人物/事件 JSONL；有最多 16 项进程内上下文缓存，命中时可复用上下文。读取评审还会校验历史哈希链。此路径未设置类似 trajectory 的明确文件字节数上限。GET 不追加评审、也不写回任务 summary 或模型结果；SQLite 连接会执行 `PRAGMA journal_mode=WAL`，因此不能把它理解成“完全不触碰存储文件”。目录初始化在应用 lifespan 完成。来源：[src/rallymate_service/technical_review.py:164](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:164)、[src/rallymate_service/technical_review.py:284](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:284)、[src/rallymate_service/technical_review.py:299](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:299)、[src/rallymate_service/technical_review.py:312](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:312)。

<a id="route-15"></a>

### `POST /v1/jobs/{job_id}/technical-review` 提交或修订人工复核

用途：新增人工评审或为已有评审追加修订版本。定义：[src/rallymate_service/api.py:1071](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1071)；输入模型：[src/rallymate_service/technical_review.py:73](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:73)；业务校验与写入：[src/rallymate_service/technical_review.py:390](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:390)。

**请求体：JSON 对象，最多 32,768 字节（32 KiB）。** 路由按流累计原始字节，超限立即 `413`。使用严格 Pydantic 类型，不把字符串数字或布尔值自动转成整数；未知字段、重复 JSON 键、非有限数值、损坏 JSON 均拒绝。建议发送 `Content-Type: application/json`；当前实现直接解析原始 body，未单独以 Content-Type 做 `415` 判定。

|字段|必填/默认|类型与限制|
|---|---|---|
|`mutation_id`|必填|小写标准 UUID，版本段允许 1～5；同一次完全相同请求重试沿用它|
|`expected_revision`|必填|严格整数，≥0；最近 GET 响应的顶层 `revision`|
|`source_sha256`|必填|64 位小写十六进制；复制 GET 响应|
|`source_reference_version`|必填|字符串，必须等于当前规则包版本|
|`source_reference_sha256`|必填|64 位小写十六进制；复制 GET 响应|
|`artifact_context_sha256`|必填|64 位小写十六进制；复制 GET 响应|
|`review_id`|可省略，默认 null|修改已有评审时传其小写 UUID；新增时 null/省略|
|`target_kind`|可省略，默认 `indicator`|`indicator` 或 `visual_rule`|
|`indicator_id`|可省略，默认 null|非 null 时长度 1～64；指标评审必须为目录中 ID，且 `visual_rule_id=null`|
|`visual_rule_id`|可省略，默认 null|非 null 时长度 1～128；视觉规则评审必须为目录中 ID，且 `indicator_id=null`|
|`player_id`|必填|严格整数，≥1，且在 GET 的人物集合中|
|`event_id`|可省略，默认 null|字符串最长 128；系统事件模式须能查到匹配事件，人工时段模式须 null|
|`event_source`|必填|`system_event` 或 `manual_interval`|
|`start_ms`|必填|严格整数，≥0|
|`end_ms`|必填|严格整数，>0；同时必须 `start_ms < end_ms`|
|`reviewer_id`|必填|字符串长度 1～128，自报评审者 ID|
|`reviewer_name`|必填|字符串长度 1～128，自报姓名|
|`reviewer_role`|必填|`coach` 或 `reviewer`；不会因此获得独立权限|
|`observability`|必填|`observable`、`partial`、`unobservable`|
|`status`|必填|`graded`、`observed`、`not_observed`、`unassessable`，还须满足目标类型规则|
|`grade`|**必填，可为 null**|`A|B|C|D|E|null`；声明可空不代表可省略|
|`reason_zh`|必填|字符串长度 2～4000|
|`next_step_zh`|**必填，可为空字符串**|字符串最长 4000|

`reviewer_id`、`reviewer_name`、`reason_zh`、`next_step_zh` 必须已去除首尾空白；禁止 ASCII <32 的控制字符，但允许换行和 Tab。

**业务关系约束。**

1. 提交的视频、来源规则和产物上下文指纹必须与服务端当前值一致。时间区间须同时完整落在已分析视频范围和该人物已记录范围内。
2. `indicator`：只接受 `status=graded|unassessable`。`graded` 必须 `manual_grading_allowed=true`、`grade` 非 null、`observability=observable`；其余状态的 grade 必须 null。
3. `visual_rule`：只接受 `observed|not_observed|unassessable`，必须使用 `manual_interval` 且 `grade=null`；确认“出现/未出现”时必须 `observable`。视觉条目不能产生 A～E。
4. `system_event`：事件必须存在，事件 code 必须等于指标规则的 event code，人物必须相同，提交区间必须位于事件区间内。`manual_interval` 必须不带 event_id。
5. 修订不能改来源、目标类型/指标/视觉规则、人物、事件来源/编号、时段或评审者 ID/姓名/角色；这些绑定变化应新增评审。允许修订的是可观察性、结论、等级、理由和下一步。不同评审者可独立提交，不做多数表决。

示意请求（占位指纹必须替换为 GET 返回值，示意时段及指标也须与任务上下文匹配）：

```json
{
  "mutation_id": "11111111-1111-4111-8111-111111111111",
  "expected_revision": 0,
  "source_sha256": "<GET返回的64位指纹>",
  "source_reference_version": "scoring-reference-20261004",
  "source_reference_sha256": "<GET返回的64位指纹>",
  "artifact_context_sha256": "<GET返回的64位指纹>",
  "review_id": null,
  "target_kind": "indicator",
  "indicator_id": "FS01-M02",
  "visual_rule_id": null,
  "player_id": 1,
  "event_id": null,
  "event_source": "manual_interval",
  "start_ms": 1000,
  "end_ms": 1800,
  "reviewer_id": "reviewer-01",
  "reviewer_name": "示例评审者",
  "reviewer_role": "reviewer",
  "observability": "partial",
  "status": "unassessable",
  "grade": null,
  "reason_zh": "脚部有遮挡，无法完整核对该时段。",
  "next_step_zh": "补充无遮挡的视频后复核。"
}
```

**成功：`200`，并非 201；`Cache-Control: no-store`。** 返回与 GET 相同的完整上下文和最新 entries。写入 `data_root/technical-reviews.sqlite3`，历史表只追加；数据库触发器禁止 UPDATE/DELETE。新提交令全任务 `revision` +1，修订还令该条 `entry_revision` +1。相同 job 下，同一 `mutation_id` 且整个规范化 payload 哈希相同，返回当前结果而不再追加；不同内容复用该 ID 则 `409 mutation_conflict`。重试仍先执行当前来源/上下文和业务验证，来源已变化不保证旧重试成功。

**错误表。** 除 GET 的上下文/来源/历史错误和公共鉴权错误外：

|状态码|`detail.code`|
|---|---|
|413|`review_too_large`|
|422|`invalid_review`；`unknown_indicator`；`invalid_indicator_status`；`unknown_visual_rule`；`invalid_visual_review`；`visual_without_observation`；`invalid_interval`；`unknown_player`；`invalid_player_interval`；`event_binding_mismatch`；`manual_interval_has_event`；`source_rule_blocked`；`grade_without_observation`；`unassessable_has_grade`|
|409|`source_changed`；`source_reference_changed`；`artifact_context_changed`；`mutation_conflict`；`revision_conflict`；`immutable_binding`；`review_already_exists`|
|404|任务不存在（字符串 `job not found`）；`review_not_found`|

来源校验失败和 revision 冲突应重新 GET、重新核对后构造新提交；不能单纯强行加大 revision。POST 首先解析请求体再查任务，因此不存在的 job 配上损坏 body 可能先得到 422。相关既有测试：[tests/test_technical_review.py:76](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:76)、[tests/test_technical_review.py:109](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:109)、[tests/test_technical_review.py:120](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:120)、[tests/test_technical_review.py:126](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:126)、[tests/test_technical_review.py:153](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:153)、[tests/test_technical_review.py:179](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:179)、[tests/test_technical_review.py:202](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:202)、[tests/test_technical_review.py:237](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:237)。

<a id="route-16"></a>

### `GET /v1/jobs/{job_id}/technical-review/export` 导出复核记录与候选标签

用途：导出当前评审与完整追加历史，用于离线复核或后续人工标定准备。定义：[src/rallymate_service/api.py:1087](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1087)；生成：[src/rallymate_service/technical_review.py:351](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:351)。

请求、任务状态、鉴权和上下文错误与 GET technical-review 相同。成功 `200 application/json`，`Cache-Control: no-store`，`Content-Disposition: attachment; filename="technical-reviews.json"`。文件名固定，不根据客户端输入构造。

除普通 GET 的所有字段外，增加：

|字段|含义|
|---|---|
|`history[]`|全部持久化记录，按 `job_revision` 顺序；保留历史 hash 链。`source_binding_current` 是最新 entries 读取时计算的标志，history 原记录不附加此标志|
|`exported_at`|导出时 UTC ISO 时间|
|`hash_is_identity_signature_or_trusted_timestamp`|固定 false；记录哈希不能证明评审者身份或可信时间|
|`coach_label_candidates[]`|仅从当前来源绑定仍有效且 `status=graded` 的最新评审生成候选|
|`intake_requirements[]`|需核实来源/人物/事件/阶段、独立盲评与争议裁定、按视频来源划分标定与测试、提交原始材料并通过 intake/可信授权等说明|

每个候选包含 `coach_label`（`schema_version=1.0.0`、`annotation_id=<review_id>:r<entry_revision>`、`video_id=source_sha256`、`event_id`、`indicator_id`、`annotator_id`、`label_type=grade`、`grade`）、`review_id`、`review_record_sha256`、`event_source`、`event_binding`（`source_sha256,event_code,player_id,start_ms,end_ms`），以及固定 false 的 `accepted_manual_event`、`human_truth_ready`、`calibration_eligible` 和同前的 `calibration_status`。人工时段的候选 event_id 是服务端从绑定计算出的 `manual-interval-...`。导出不会改写 scores、触发模型、自动裁定或自动导入真值包。

<a id="route-17"></a>

### `GET /v1/jobs/{job_id}/demo-result` 构建前端综合分析报告

用途：为前端组合一次已完成分析的训练反馈、逐项测量、机器动作候选、复核材料和资源链接。定义：[src/rallymate_service/api.py:1098](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1098)；主构造器：[src/rallymate_service/user_demo.py:381](C:/Users/Admin/Documents/网球/src/rallymate_service/user_demo.py:381)。

**参数。** `job_id` 路径字符串；`report_contract` 查询参数默认 `legacy-v1`。只接受 `succeeded` 任务，且构造器要求 summary 的 `status=completed`、非空字符串 `job_id`。

**成功 `200 application/json`。此路由没有显式设置 Cache-Control。** 原生顶层结构如下，嵌套 footwork/motion 会受兼容参数影响：

|字段|类型/主要内容|
|---|---|
|`schema_version`、`result_version`|`1.2.0`；`rallymate-user-demo-result-v1.5.0`|
|`result_kind`、`status`、`job_id`|`real_video_training_feedback_preview`、`ready`、任务标识|
|`measurement_contract`|summary.minimum_scoring_loop.coordinate_contract 的对象副本|
|`measurement_update_required`|合同版本不是 `isotropic-frame-long-edge-v1.1.0` 时 true；旧值保留供复核|
|`measurement_warnings_zh[]`、`headline_zh`|动态中文测量提醒与结果标题|
|`training_evaluation`|测量证据参考评价（Beta）；见下文|
|`technique_assessment`|与专项技术评估构造器相同的主体，见独立接口章节；嵌入块不自动增加独立路由的 `catalog_url`/`trajectory_url`|
|`hit_statistics`|状态、nullable 击球计数与候选统计，见下文|
|`action_recognition`|机器候选及 `motion_analysis`；与嵌入技术评估的对应块相同|
|`trajectory_analysis`|轨迹资源能力摘要；不直接包含轨迹点|
|`final_demo_score`|动作信息成型参考分；不是正式技评分|
|`analysis_quality`、`display_score`|分析完成度；后者为兼容别名|
|`formal_scoring`|`available=false`、`score_0_to_100=null`、`grade=null`、`status=calibration_required`、`message_zh`|
|`actions[]`|FS01、FS02、FS09 三类候选动作及测量摘要|
|`model`|`pose_preset`、`pose_format`|
|`runtime`|`accelerator`（GPU/CPU）、`device_name`、`device_used`、`pose_backend`、`pose_profile`、`cpu_thread_limit`、`annotated_video_generated`、`annotated_video_compatibility`|
|`safety`|真实上传数据、未编造等级、参考分非正式教练分等布尔语义声明；以代码返回为准|
|`input`|可选；仅复制有效正数视频元数据 `video.duration_ms,width,height,fps,frame_count`，不复制本地路径或来源 IP|
|`artifact_urls`|当前实际存在且在白名单的产物名→URL 映射|
|`trajectory_url`、`technique_assessment_url`|关联接口 URL；有配置 public_base_url 时为带该前缀地址，否则为相对路径|
|`footwork_review`|机器事件/测量的逐段回放复核材料，并非人工 technical-review 数据|
|`source_aligned_assessment`|绑定来源规则的局部机器测量|
|`analysis_report`|用户可读的综合报告索引与观测建议|

`training_evaluation`：包含 `evaluation_version=rallymate-training-evaluation-beta-v1.3.0`、`score_semantics=measurement_evidence_quality`、`technical_score_0_to_100=null`、`technical_score_status=calibration_required`、`technical_grade=null`；以及 `available`、`score_0_to_100`（可空）、`score_explanation`、`label_zh`、`level_zh`、`summary_zh`、`meaning_zh`、`aggregation`、`evaluated_indicator_count`、`total_indicator_count`、`strengths_zh=[]`、`priorities_zh=[]`、`action_evaluations`、`indicator_evaluations[]`、`component_weights`、`formal_grade=null`、`is_formal_coach_score=false`。`input_validation` 给出 `duplicate_record_count`、`conflicting_record_count`、`event_code_mismatch_record_count`、`unidentified_record_count`。动作映射键为 FS01/FS02/FS09；每项汇总可用性、参考分、计数、文本及 `indicator_evaluations`。指标项补充 `indicator_id,event_code,name_zh,definition_zh,measurement_status,observation_zh,suggestion_zh,measured_instance_count,total_instance_count,components,effective_component_weights,repeatability_status,repeatability_sample_count,representative_measurements,limitations_zh` 等解释字段。动态测量名称、单位、数值取决于指标注册和真实证据，不能按“所有指标都测齐”处理。来源：[src/rallymate_service/training_evaluation.py:15](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:15)、[src/rallymate_service/training_evaluation.py:579](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:579)、[src/rallymate_service/training_evaluation.py:641](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:641)。

`actions[]` 每项包含 `event_code,name_zh,status,detected_segments,registered_indicator_count,measured_indicator_count,measurement_coverage_percent,amplitudes,formation_assessment,performance_assessment,indicator_evaluations,summary_zh,feedback_zh`。状态为 `not_observed|partially_measured|measured`；“未观测”不证明动作没有发生，“measured”也不证明技术类型已经人工确认。`amplitudes` 是已验证指标的幅度代表测量；`formation_assessment` 描述轮廓与幅度信息是否成型；`performance_assessment` 实际承载同一测量证据参考评价，不能仅根据字段名推断技术表现。来源：[src/rallymate_service/user_demo.py:442](C:/Users/Admin/Documents/网球/src/rallymate_service/user_demo.py:442)。

`final_demo_score`：`label_zh,value_0_to_100,score_semantics,meaning_zh,semantics,score_version,aggregation,action_reference_scores,evaluated_action_count,total_action_count,action_component_weights,is_formal_technique_score,is_coach_score,is_recognition_accuracy`。分数只平均有值的动作参考分，无可用动作时 null；后三个身份标志均 false。`analysis_quality` 包含 `label_zh,value_0_to_100,score_semantics,headline_zh,meaning_zh,components,component_weights,is_formal_technique_score`；components 为已观测动作家族比例、已测唯一指标比例、平均动作测量覆盖比例。`display_score` 提供同值及 `compatibility_alias_for=analysis_quality`。来源：[src/rallymate_service/user_demo.py:602](C:/Users/Admin/Documents/网球/src/rallymate_service/user_demo.py:602)。

`hit_statistics`：`status=candidates_only|unsupported`，`total_count,shot_count,contact_count` 当前均 null；`candidate_count,candidate_by_family,by_event_code,upstream_action_event_counts,reason_zh,count_semantics` 解释候选和 GS 事件。球/拍观测、挥拍候选、GS 动作事件均不等于确认触球。`trajectory_analysis` 包含 `status,source_artifact,endpoint_required,ball_observation,racket_observation,reason_zh`；需再调用 trajectory 获取点。来源：[src/rallymate_service/user_demo.py:115](C:/Users/Admin/Documents/网球/src/rallymate_service/user_demo.py:115)。

`footwork_review` 原生 1.1 包含 `schema_version,review_version,status,reason,reason_zh,episodes,episode_count,returned_episode_count,is_truncated,measurement_summary,limitations_zh`；构造成功还含 `rejected_event_count,rejected_indicator_count`。每个 episode 具有 `event_id,video_id,event_code,name_zh,person_track_id,start_ms,end_ms,indicators`。每项 indicator 具有 `indicator_id,name_zh,feature_status,scoring_status,features,measurements,measurement_status,measured_feature_count,expected_feature_count`；每条独立 measurement 有 `feature_name,name_zh,value,unit,confidence,status,reason_codes,reason_zh,review_hint_zh,window,source_frames,feature_version,review_version,required_joints,view_semantics,view_label_required,aggregate_feature_status`。窗口含 `start_ms,end_ms,scope`。最多读取 16 MiB 事件文件、5000 事件/行、65000 指标记录，返回最多 300 个 episode；`measurement_summary.scope=all_bound_episodes_including_truncated` 指明计数范围可大于已返回列表。损坏或缺少可选事件产物让此子块 unavailable，不使整个报告失败。来源：[src/rallymate_service/footwork_review.py:23](C:/Users/Admin/Documents/网球/src/rallymate_service/footwork_review.py:23)、[src/rallymate_service/footwork_review.py:63](C:/Users/Admin/Documents/网球/src/rallymate_service/footwork_review.py:63)、[src/rallymate_service/footwork_review.py:235](C:/Users/Admin/Documents/网球/src/rallymate_service/footwork_review.py:235)、[src/rallymate_service/footwork_review.py:318](C:/Users/Admin/Documents/网球/src/rallymate_service/footwork_review.py:318)、[src/rallymate_service/footwork_review.py:377](C:/Users/Admin/Documents/网球/src/rallymate_service/footwork_review.py:377)。

`source_aligned_assessment` 当前只涉及 `FS01-M02`、`FS01-M04`、`FS01-M05`、`FS09-M05`。字段为 `version,status,score_semantics,technical_grade=null,technical_score_0_to_100=null,indicators,reason_zh`，可用投影补充 `measurement_artifact_version,source_binding`。每个指标给出 `indicator_id,window_count,measured_window_count,unavailable_window_count,returned_window_count,is_truncated,selection_policy,windows`；每个窗口含事件/人物/起止时间、`measurements`、`visibility`、`limitations_zh,association,complete_source_rule_verified=false`。每个 measurement 为 `feature_name,label_zh,value,unit,status,reason_zh,source_requirement_zh,source_ref,measurement_window`。visibility 给出该指标所需关节的有效帧数、总数、比例、0.7 门槛及原因，过门槛只代表可见性。绑定当前事件、人物、frames 文件指纹；源测量文件上限 64 MiB、事件上限 16 MiB，每指标最多 80 窗口（优先最多 60 可测+20 不可测，再按时间补足）。损坏、缺失、绑定不一致降级 unavailable。来源：[src/rallymate_service/source_assessment.py:13](C:/Users/Admin/Documents/网球/src/rallymate_service/source_assessment.py:13)、[src/rallymate_service/source_assessment.py:160](C:/Users/Admin/Documents/网球/src/rallymate_service/source_assessment.py:160)、[src/rallymate_service/source_assessment.py:207](C:/Users/Admin/Documents/网球/src/rallymate_service/source_assessment.py:207)、[src/rallymate_service/source_assessment.py:224](C:/Users/Admin/Documents/网球/src/rallymate_service/source_assessment.py:224)。

`analysis_report`：`version,headline_zh,score_semantics,reference_score_0_to_100,technical_score_0_to_100=null,technical_score_status=calibration_required,focus_areas,layers,measurement_summary,view_policy,video_specs,recognition_backend,recommendation_semantics`。`focus_areas` 最多 3 条；`layers` 是识别、测量、参考评价分层状态和来源；计数范围为已返回回放片段，注意与 footwork 的全范围统计区别。建议语义是证据复核与补拍，不是自动诊断技术错误。来源：[src/rallymate_service/analysis_report.py:247](C:/Users/Admin/Documents/网球/src/rallymate_service/analysis_report.py:247)。

**错误与空结果。** `404 job not found`；`409 job has not succeeded`；绑定的技术注册表与提交时不同则 `409` 对象，`code=technique_registry_changed_since_submission`，附 `message,expected,actual`；`UserDemoResultError`、技术目录错误或被捕获的 OSError 返回 `500`，detail 字符串前缀 `demo result is unavailable:`。公开/生产错误经 `_public_error_detail` 脱敏。缺少或零字节的 indicator-features 文件被视为零条记录，允许 `200` 返回证据不足；非空文件解析后零条记录、非对象条目、损坏 JSON、重复键或显式 NaN/Infinity 会失败。业务字段不完整的对象可能被下游忽略或记入 `input_validation`，不是所有无有效测量的非空文件都会报错。其他未捕获异常不能当成稳定业务错误合同。来源：[src/rallymate_service/api.py:1101](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1101)；[src/rallymate_service/user_demo.py:183](C:/Users/Admin/Documents/网球/src/rallymate_service/user_demo.py:183)。

**读取与副作用。** 从任务数据库 summary 及本地结果文件组装响应；必要时调用 `summary_with_actions`，基于 frames 与主球员时间线补算当前版本动作候选，使用最多 12 项进程内缓存，未加载 GPU 模型、未持久化回数据库或原始产物。指标记录加载器支持 JSONL，也支持 JSON 数组、单对象，或 `records|indicator_features|predictions|detections|actions|events` 下的数组；该加载器一次读取整个文件，没有单独字节上限。人工评审数据库不会被此接口合并入机器报告。来源：[src/rallymate_service/api.py:390](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:390)；[src/rallymate_scoring/stroke_candidates.py:455](C:/Users/Admin/Documents/网球/src/rallymate_scoring/stroke_candidates.py:455)。

<a id="route-18"></a>

### `GET /v1/jobs/{job_id}/trajectory` 读取球与球拍轨迹预览

用途：从 `frames.jsonl` 读取球/球拍观测，生成可视化轨迹和短时常速度外推。定义：[src/rallymate_service/api.py:1160](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1160)；构造器：[src/rallymate_vision/trajectory.py:929](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:929)。

|参数|位置|默认|限制/含义|
|---|---|---|---|
|`job_id`|路径|必填|字符串|
|`sample_limit`|query|240|整数 1～1000；每条所选观测轨迹的返回点上限，不是整个文件的读取行数上限|
|`prediction_horizon_ms`|query|400|整数 0～5000；0 表示不请求外推|

只支持 `running` 和 `succeeded`。running 时允许忽略最后一个尚无换行的未完成 JSONL 行，但损坏的已完成行仍报错；`source.is_partial=true`。succeeded 时完整读取校验。无观测也可返回 `200 status=not_observed`。

**成功 `200`，`Cache-Control: no-store`。** 顶层：`schema_version=1.0.0`、`result_kind=trajectory_and_racket_observability_preview`、`status=ready|not_observed`、`job_id`、`source`、`ball`、`racket`、`limitations[]`。

- `source`：`frames_path` 固定为公开名称 `frames.jsonl`；`frames_schema_versions[]`、`frame_count,width,height,coordinate_space=normalized_frame_0_1,timebase=source_video_timestamp_ms,is_partial,start_timestamp_ms,end_timestamp_ms`。
- ball/racket 共同字段：`track_id`（可空）、`track_id_status=stable_id|untracked_observations|not_observed`、`track_count,observed_count,coverage_fraction,first_timestamp_ms,last_timestamp_ms,longest_missing_run_frames,missing_run_semantics,confidence,observed[]`。confidence 为 `mean,median,min,max`，无观测时均 null；observed_count 是采样前数目。缺帧跨度含义为所选轨迹自身区间，不是整段视频缺帧数。
- 观测点：`frame_index,processed_index,timestamp_ms,x,y,confidence,track_id`；球拍点还可能有 `bbox=[x1,y1,x2,y2]`。坐标归一化到画面，不是球场米制坐标。
- `ball` 另有 `reconstruction,prediction_status,prediction_reason,prediction_horizon_ms,predicted[],predicted_covered_horizon_ms,velocity,semantics`。prediction_status 为 `heuristic_preview|not_requested|not_available`；缺身份、不足两点、无有效近期时间段都不能外推。predicted 每点为 `timestamp_ms,x,y,clamped_to_frame,source=constant_velocity_extrapolation`，最多 8 点；velocity 可 null，否则含 `vx_normalized_per_s,vy_normalized_per_s,speed_normalized_per_s,direction_image_deg,segment_count,source`。
- `ball.reconstruction`：`version=1.1.0,status,coordinate_space,segments,summary,limitations_zh`。segment 包含 `segment_id,track_ids,start_ms,end_ms,observed_count,interpolated_count,bridged_gap_count,max_bridged_gap_ms,interpolation_intervals,points,display_quality,analysis,sampling,interpolation_intervals_complete`。观测与插值有不同 source 标记。display_quality 解释置信度、观测/跟踪比例及 `active_ball_identity=unconfirmed`；analysis 仅为画面归一化位移、长度、速度、方向、运动状态；sampling 告知原点数、返回数、是否采样。summary 含轨迹段/点/真实观测/插值/桥接/拒绝点等计数、覆盖和置信度。最多 1000 段，总点预算不超过 12000，不能把 sample_limit 当作所有重建点总数。
- `racket` 另有 `geometry_status=bbox_only`、`association_status=unassociated`、`association_reason`、`recognition_status=generic_bbox_detection|not_observed`、`keypoint_status=not_available_from_current_detection_contract`、`prediction_status=not_available`、`prediction_reason`、`semantics`。没有拍头/拍柄/甜区关键点，也没有与目标球员的确定关联。

**错误。** `404 job not found`；`409 trajectory requires a running or succeeded job`；缺 frames 返回 `409 {code:trajectory_requires_frames_artifact,message,required_artifact:frames.jsonl}`；参数不合规 `422`；提取器拒绝产物则 `422 {code:trajectory_artifact_invalid,message}`，message 在公开/生产环境脱敏。路由中的独立 `stat()` 等未统一包装所有 OSError，不能保证所有文件系统异常都有上述结构。

**边界与副作用。** 输入最多 256 MiB、2,000,000 行、500,000 观测点；按读取开始时文件大小读取快照，避免随 worker 追加无限读取。进程内 LRU 缓存最多 6 个键，键包含文件路径、mtime、size、任务状态和两个 query 参数；HTTP no-store 与此服务端计算缓存并不矛盾。不写数据库、不改源 frames、不运行检测模型。来源：[src/rallymate_vision/trajectory.py:28](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:28)、[src/rallymate_vision/trajectory.py:245](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:245)、[src/rallymate_vision/trajectory.py:411](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:411)、[src/rallymate_vision/trajectory.py:471](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:471)、[src/rallymate_vision/trajectory.py:843](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:843)、[src/rallymate_vision/trajectory.py:1045](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:1045)；既有测试：[tests/test_trajectory_api.py:103](C:/Users/Admin/Documents/网球/tests/test_trajectory_api.py:103)、[tests/test_trajectory_api.py:195](C:/Users/Admin/Documents/网球/tests/test_trajectory_api.py:195)、[tests/test_trajectory_api.py:230](C:/Users/Admin/Documents/网球/tests/test_trajectory_api.py:230)。

<a id="route-19"></a>

### `GET /v1/jobs/{job_id}/pose-preview` 读取骨架回放窗口

用途：读取一个有上限的源视频时间窗口，返回已观察到的人体骨架，供回放叠加；不重新跑姿态推理。定义：[src/rallymate_service/api.py:1236](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1236)；构造器：[src/rallymate_vision/pose_playback.py:167](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:167)。

|参数|默认|限制|
|---|---|---|
|`job_id`（path）|必填|字符串|
|`start_ms`（query）|0|整数 ≥0，源视频时间；本路由没有显式的“不得大于总视频时长”校验|
|`duration_ms`（query）|10000|整数 1～10000|
|`sample_limit`（query）|600|整数 1～600|

支持 `running|succeeded`，必须存在 frames；primary-player 文件是可选的。有主球员时间线时要求帧索引、processed_index、时间戳和选择身份精确绑定，歧义不借用其他人；没有时间线时只接受单个可见人物。无可靠姿态的帧可返回空 `keypoints` 和原因，而不是制造骨架。

**成功 `200`，`Cache-Control: no-store`。**

|字段|内容|
|---|---|
|`schema_version,version,result_kind`|`1.0.0`、`observed-pose-playback-v1.0.0`、`observed_pose_playback`|
|`job_id`|任务 ID|
|`coordinate_space,joint_schema`|`normalized_frame_0_1`、`halpe26_named`|
|`keypoint_names[],skeleton_edges[]`|支持关节名及由两个关节名组成的连线|
|`min_keypoint_confidence,interpolation`|0.35、`none`|
|`frames[]`|按源时间排列的观测，最多 sample_limit；字段见下文|
|`source`|来源、请求窗口、尺寸、数量、采样与人物选择信息|

每帧：`frame_index,processed_index,timestamp_ms,valid_until_ms,person_track_id,selection_epoch,selection_status,width,height,source_keypoint_format,keypoints,reason_code`。每个 keypoint：`name,x,y,confidence`。仅返回置信度 0.35～1、画面内且坐标有效的命名关节；无效点被省略，不补齐、不夹紧到边界。原始帧的 `frame.timestamp_source == "fps_fallback"` 时清空点；采样不延长观测有效期。`selection_epoch` 在人物改变、明显时间断层或 processed_index 跳变时变化，前端不能跨 epoch 连成同一个人。

`source` 字段：`frames_path=frames.jsonl,primary_timeline_path,requested_start_ms,requested_end_ms,width,height,observed_frames_in_window,returned_frames,includes_preceding_observation,is_sampled,is_partial,timestamp_semantics=source_frame_timestamp_ms,min_keypoint_confidence,selection_source,max_display_age_ms=100`。主时间线名缺失时 null；selection_source 为 `primary_player_timeline|single_visible_person_only`。为覆盖窗口边缘，可能包含一个 start_ms 前仍有效的同身份观测，因此 returned_frames 不一定等于 observed_frames_in_window。没有观测的窗口也可成功返回空数组。

**错误。** `404 job not found`；任务状态不合要求 `409 pose playback requires a running or succeeded job`；缺 frames `409 {code:pose_playback_requires_frames_artifact,required_artifact:frames.jsonl}`；参数不合法 `422`；解析/边界/文件读取失败 `422 {code:pose_playback_artifact_invalid,message:"Pose playback source could not be verified."}`，不回显本地路径。

**读取边界与副作用。** frames 最大 1 GiB、primary 最大 256 MiB；最多 2,000,000 行、单行最多 1 MiB、单窗口最多 10,000 源帧。frames 的源索引、processed_index 和时间戳须严格递增；primary processed_index 也须严格递增，拒绝重复键、非有限数值。running 只容忍最后未换行的尾部。缓存最多 16 项，键包括两个文件的签名、job 状态和三个参数。只读现有产物和进程内缓存，不保存新骨架。来源：[src/rallymate_vision/pose_playback.py:18](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:18)、[src/rallymate_vision/pose_playback.py:73](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:73)、[src/rallymate_vision/pose_playback.py:109](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:109)、[src/rallymate_vision/pose_playback.py:145](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:145)、[src/rallymate_vision/pose_playback.py:167](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:167)、[src/rallymate_vision/pose_playback.py:256](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:256)；既有测试：[tests/test_pose_playback.py:179](C:/Users/Admin/Documents/网球/tests/test_pose_playback.py:179)、[tests/test_pose_playback.py:203](C:/Users/Admin/Documents/网球/tests/test_pose_playback.py:203)、[tests/test_pose_playback.py:224](C:/Users/Admin/Documents/网球/tests/test_pose_playback.py:224)。

<a id="route-20"></a>

### `GET /v1/jobs/{job_id}/technique-assessment` 评估各技术动作的证据

用途：围绕已注册技术目录返回证据就绪度、阶段状态和局部观测限制。定义：[src/rallymate_service/api.py:1279](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1279)；构造器：[src/rallymate_scoring/technique_assessment.py:707](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:707)。

参数为路径 `job_id`、query `report_contract=legacy-v1|current`（默认 legacy-v1）。任务须 `succeeded`。成功 `200 application/json`，**没有显式 Cache-Control**。

|顶层字段|含义|
|---|---|
|`assessment_version`|`rallymate-technique-assessment-v1.4.0`|
|`registry_version`、`registry_snapshot`|当前目录版本和提交快照核验；snapshot 为 `status=verified|legacy_unpinned`、`expected`（旧任务可 null）、`actual`；两份身份含 registry_id/version/sha256|
|`job_id`|来自 summary 的 job_id|
|`overall_evidence_score_0_to_100`|只平均已观测技术的证据分；无已观测技术为 null|
|`formal_score_available`、`formal_score_message_zh`|false 和未启用数值权重/教练标定的解释|
|`coverage`|证据名→整数百分比；如 pose、tracking、ball、racket、court|
|`coverage_detail`|每种证据含 `fraction,percent,source,field,scope,source_kind,fallback_used`，可能附补充记录来源；用于解释覆盖的范围和回退|
|`coverage_source`|证据名→来源字符串|
|`family_summary`|家族名→汇总及识别状态；键和名称由目录驱动|
|`action_recognition`|机器动作候选、计数、按家族分布、接触语义和 motion_analysis；不是人工评审|
|`techniques[]`|每个注册技术一项，详见下文；数量由当前有效目录决定|
|`policy`|version、contact 策略、coverage 策略、readiness 门槛、formal_score 条件|
|`safety`|固定声明：非正式技术分、非教练分、缺证据不补值、contact 需要球与拍、contact 不等于准确率|
|`catalog_url`、`trajectory_url`|目录和此任务轨迹接口链接|

每个 technique：`technique_id,family,family_name_zh,name_zh,status,observed,candidate_count,candidate_intervals,recognition_status,evidence_score_0_to_100,score_0_to_100=null,formal_grade=null,phase_statuses,evidence,key_field_analysis,core_visual_features,reference_constraints,limitations_zh,semantics=evidence_readiness_reference_only`。

- `status`：未观测为 `not_observed`；已观测时按必需证据最低覆盖率 `<0.4` 为 `unavailable`，`<0.72` 为 `partial`，否则 `ready`。这些是服务运行门槛，不是教练成绩界限。
- `recognition_status`：`observed|candidate|not_observed`。家族级候选不能自行证明细分技术；只有发球条目按当前实现附对应家族候选，其他家族候选主要展示在 family_summary。
- `evidence_score_0_to_100`：已观测时由必需证据最低覆盖×0.7 + 增强证据平均覆盖×0.3 计算；未观测固定 0。这是证据分，与 `score_0_to_100=null` 的正式技术分分开。
- `phase_statuses[]`：`phase,status=measured|proxy|unavailable,evidence_source`。只有明确绑定该技术的阶段记录才作为 measured；仅事件存在时为 proxy。
- `evidence`：`required_coverage,enhanced_coverage` 两个百分比映射；`contact_status,contact_policy_version,event_codes`。球/拍观测未有显式事件或人物关联时不冒充已确认接触。
- `key_field_analysis`：`required_fields,enhanced_fields,missing_required_fields`；每个证据项有 `field,field_label_zh,required,coverage_percent,status`。
- `family_summary[family]`：`name_zh,observed_count,total_count,evidence_score_0_to_100,candidate_count,candidate_intervals`，以及随家族观测/候选情况变化的识别状态和解释字段；`action_recognition.motion_analysis` 的片段/指标名是版本化动态结构，不保证每个家族都有可评价片段。

**错误。** `404 job not found`；`409 job has not succeeded`；注册表快照变化 `409 {code:technique_registry_changed_since_submission,message,expected,actual}`；非空指标文件非法 `422`，detail 前缀 `technique evidence is invalid:`；构造阶段的 ValueError 或技术目录错误 `503`，前缀 `technique assessment is unavailable:`；query 枚举错误 `422`。缺少或零字节指标文件允许用空记录继续；没有证据不伪造分数。某些文件 stat 等未捕获 OSError 仍可能成为一般服务错误。

**读取与副作用。** 与 demo-result 一样会按需要补算并缓存动作候选，读取指标记录和当前目录；核验提交时固定的注册表身份。没有写回数据库/原产物、没有写入人工复核。来源：[src/rallymate_scoring/technique_assessment.py:219](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:219)、[src/rallymate_scoring/technique_assessment.py:525](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:525)、[src/rallymate_scoring/technique_assessment.py:558](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:558)、[src/rallymate_scoring/technique_assessment.py:650](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:650)、[src/rallymate_scoring/technique_assessment.py:773](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:773)、[src/rallymate_scoring/technique_assessment.py:821](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:821)；既有测试：[tests/test_technique_assessment.py:49](C:/Users/Admin/Documents/网球/tests/test_technique_assessment.py:49)、[tests/test_technique_assessment.py:224](C:/Users/Admin/Documents/网球/tests/test_technique_assessment.py:224)。

<a id="route-21"></a>

### `GET /v1/jobs/{job_id}/artifacts/{artifact_name}` 预览或下载分析产物

用途：下载某任务已经生成的白名单原始产物；也可对 summary 请求旧版展示投影。定义：[src/rallymate_service/api.py:1330](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1330)；名单：[src/rallymate_service/api.py:79](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:79)。

|参数|位置|默认/限制|
|---|---|---|
|`job_id`|路径|字符串；通过数据库取该任务的 output_dir|
|`artifact_name`|路径|必须精确属于下表 19 个名称；不接受任意路径或任意扩展名|
|`report_contract`|query|`current`（默认）或 `legacy-v1`；只对 summary.json 的 legacy 请求改变内容|

任务必须 `succeeded`，文件必须存在。**成功通常为 `200` 文件响应**，MIME 由 FileResponse 按文件名推断，不把全部文件都包装成 JSON；底层 FileResponse 的条件/范围请求行为取决于项目使用的框架版本，这个路由未自定义 Range 协议。所有成功下载明确 `Cache-Control: private, no-store`。

|白名单文件|用途/内容边界|呈现|
|---|---|---|
|`summary.json`|整体分析汇总、运行/模型/输入元数据、处理状态和产物索引；默认原始字节|attachment|
|`frames.jsonl`|逐帧元数据、检测框、跟踪/姿态等原始观测；trajectory/pose 的来源|attachment|
|`annotated.mp4`|带分析标注的视频，可直接预览|inline|
|`preview.jpg`|视频/分析预览图；虽为图片仍指定下载 disposition|attachment|
|`scoring-readiness.json`|结合评分卡、该视频逐帧观测与运行评分上下文生成的评分就绪审计，不等于技术得分|attachment|
|`analysis-report.html`|整体可读分析报告|inline|
|`primary-player.jsonl`|逐帧主球员选择与身份时间线|attachment|
|`primary-player-summary.json`|主球员选择与跟踪诊断汇总|attachment|
|`events.jsonl`|分析生成的动作事件与时段记录；机器候选不自动等于人工真值|attachment|
|`features.jsonl`|闭环事件特征记录|attachment|
|`indicator-features.jsonl`|按评分指标组织的特征、测量状态和证据记录|attachment|
|`scores.jsonl`|评分器输出的分值/评分状态记录；可能仍 calibration_required/unavailable，须读实际状态|attachment|
|`event-feature-errors.json`|事件特征处理错误记录|attachment|
|`scoring-loop-summary.json`|最小评分闭环运行汇总及可追溯信息|attachment|
|`scoring-loop-report.html`|版本化 Pose 指标闭环可读报告|inline|
|`calculation-readiness.json`|本视频逐指标计算就绪度、特征完整性和阻断原因|attachment|
|`indicator-measurement-portfolio.json`|按观测质量选择的各指标代表候选/真实特征值、单位、置信度和证据帧；不是技术等级|attachment|
|`scoring-cycle-measurement.json`|同一次 FS01→FS02→FS09 周期的测量完整性；禁止跨动作拼成一组完成度|attachment|
|`source-aligned-measurements.json`|按来源规则计算的局部测量产物与来源/事件/人物/帧指纹绑定|attachment|

仅“在白名单中”不表示每次任务都会生成该文件；可先看 job/demo 的 `artifact_urls` 动态映射。用途依据：[src/rallymate_scoring/loop.py:602](C:/Users/Admin/Documents/网球/src/rallymate_scoring/loop.py:602)、[src/rallymate_scoring/report.py:395](C:/Users/Admin/Documents/网球/src/rallymate_scoring/report.py:395)、[src/rallymate_scoring/source_assessment.py:17](C:/Users/Admin/Documents/网球/src/rallymate_scoring/source_assessment.py:17)、[src/rallymate_vision/pipeline.py:912](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:912)。

默认文件名为 `<job_id>-<artifact_name>`。当且仅当 `artifact_name=summary.json` 且 `report_contract=legacy-v1` 时，服务解析 JSON、执行只读投影并用 JSONResponse 返回：文件名 `<job_id>-summary-legacy-v1.json`，附 `X-RallyMate-Report-Contract: legacy-v1`；仍是 attachment、private/no-store。这个投影不是原文件的逐字节副本，原文件不会被覆盖。其他文件即使传 legacy-v1 仍按原始文件返回。

**错误与顺序。** 不在白名单 `404 artifact not found`（先于 job 查找）；任务不存在 `404 job not found`；非 succeeded `409 job has not succeeded`；文件不存在 `404 artifact not found`；legacy summary 读取或 JSON 解析错误 `422 summary artifact is invalid`；query 枚举非法 `422`。默认 current 不先验证 JSON/JSONL 内容，即使内部 JSON 损坏也可能正常下载原字节。

**文件读取边界与副作用。** 该路由从数据库可信 output_dir 拼接固定白名单名，不允许客户端指定 output_dir，也没有文件写入或模型运行。下载路径本身没有 `resolve()` 后目录包含性校验、symlink 拒绝或额外文件体积上限；它依赖任务目录/产物存储受信，不能把白名单误读成完整文件系统沙箱。默认 FileResponse 不调用公开值脱敏，原产物可能含本地路径、坐标和来源信息，所以需要鉴权；summary 的 legacy 投影也不等于路径脱敏。路由使用 `.exists()`，未在进入 FileResponse 前单独 `.is_file()` 校验，目录/竞态等异常不保证变成上述稳定 404。来源：[src/rallymate_service/api.py:1337](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1337)、[src/rallymate_service/api.py:1344](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1344)、[src/rallymate_service/api.py:1350](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1350)、[src/rallymate_service/api.py:1360](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1360)；既有测试 [tests/test_report_compatibility.py:121](C:/Users/Admin/Documents/网球/tests/test_report_compatibility.py:121)。

### 本节实现边界说明

本节根据代码和现有测试源码核对，未启动服务、加载模型、发送真实请求或修改业务代码。上述 `Cache-Control` 描述针对成功响应；给临时 `Response` 对象设置的头，在随后 `raise HTTPException` 时不应当视为错误响应头的保证。文件/数据库未捕获异常也不应被文档包装成实现中不存在的统一 JSON 错误合同。客户端接入时同时保留对一般 5xx 的处理。

## 把接口串成完整的前端业务流程

### 先区分两条访问路径

直接请求 FastAPI 和经过网页同源网关请求，看到的接口范围、错误格式、响应头可能不同。

```text
开发调试：curl / Python → FastAPI api.py → Worker 与数据库

现有网页：浏览器 → scoring-demo-web 的同源网关
                    → 网关注入后端 Bearer key
                    → FastAPI api.py → Worker 与数据库
```

下面的网关说明仅依据 `scoring-demo-web/worker/gateway.ts`，不假设其他反向代理有相同配置。它使用路由白名单，不能把后端 21 个接口都看作浏览器已经能访问的接口。

| 接口或行为 | 直接 FastAPI | 当前 Worker 网关 |
| --- | --- | --- |
| `/health/live`、`/health/ready`、`/v1/meta`、`/v1/techniques` | 按前述鉴权处理 | GET 允许转发 |
| `GET /v1/jobs` | 可以查询最近任务 | 当前不在白名单，返回 404 `route_not_allowed` |
| `GET /v1/model-capabilities` | 可以读取模型能力说明 | 当前不在白名单，返回 404 `route_not_allowed` |
| `POST /v1/jobs` | 接收 multipart 视频 | 允许转发 |
| 四个 `/v1/uploads` 接口 | 按上传协议处理 | 允许匹配对应路径和方法的请求 |
| 单任务及结果、回放、复核接口 | 按各自规则处理 | 路径 ID 符合 `[a-zA-Z0-9_-]{8,80}` 时允许规定的方法 |
| 产物下载 | 19 个名字的后端允许列表 | 只转发 `summary.json`、`frames.jsonl`、`indicator-features.jsonl`、`annotated.mp4`、`preview.jpg` |
| `/`、`/docs`、`/redoc`、`/openapi.json` | 属于 API 或其自动文档 | 不是这个分析代理白名单的入口；网站根路径由前端自身路由处理 |
| Authorization | 读取传入的 Bearer key | 不直接转发浏览器 Authorization；使用网关环境配置注入 |
| 返回错误结构 | 主要为 `{"detail": ...}` | 自身拒绝/故障为 `{"error": "..."}`；上游业务错误继续转发 |

来源：[scoring-demo-web/worker/gateway.ts:42](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/gateway.ts:42)、[scoring-demo-web/worker/index.ts:53](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/index.ts:53)。

网关对 POST/PUT 检查请求来源，跨站请求可返回 403；缺少或非法上游配置可返回 503；上游请求失败或发生未预期重定向可返回 502。分片空请求体可返回 400，接收或转发分片中断可返回 408，超过 4 MiB 可返回 413。这些属于网关行为，不是对应 Python 函数的显式错误分支。

网关会向后端转发 X-Request-ID，但当前响应头白名单没有包含 X-Request-ID、WWW-Authenticate 或 X-RallyMate-Report-Contract。直接调用 FastAPI 时可见的这些头，经该网关不保证还能在浏览器中读取。网关设置 `Cache-Control: private, no-store` 并转发媒体 Range 相关头；本文不把其行为推广到未知的其他部署代理。

前端客户端还包含 `/api/scorecard` 的调用，它属于网页侧的另一组接口，不在本文 `api.py` 的 21 个路由中。`apiClient.getCapabilities()` 默认调用 `/api/scorecard`，不能据函数名字把它误认为 `GET /v1/model-capabilities`。来源：[scoring-demo-web/app/lib/api-client.ts:184](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:184)。

### 一个视频从选择到报告的推荐顺序

以下为调用建议，不是新增后端功能。

1. 页面初始化请求 `/v1/meta` 和 `/v1/techniques`，取得版本、目录和能力声明。需要诊断时再读取 `/health/ready`，并检查 body.status。
2. 选取本地视频，保存文件身份和完整分析选项。大视频使用四步分片流程；简单脚本也可以直接 multipart 提交 `/v1/jobs`。
3. 分片上传成功后调用 complete，或从普通上传得到 202。保存返回的 `id`。上传进度与推理进度是两套数据，不应把上传 100% 显示为分析完成。
4. 轮询 `/v1/jobs/{id}?report_contract=current`。例如从约 1–2 秒一次开始，并在页面隐藏、错误重试时适度退避；这是客户端建议，后端没有规定轮询周期。
5. `queued` 显示等待，`running` 读取 `progress`。`running` 期间如有已写入的帧产物，可以尝试 pose-preview 与 trajectory；它们不是无限实时流。
6. `succeeded` 后读取 `/demo-result?report_contract=current` 构建主报告；按页面需要读取 `/technique-assessment?report_contract=current`、回放窗口和允许的产物链接。
7. `failed` 后显示任务 `error`，停止以“等待成功”为目的的常规轮询。当前没有重跑任务端点，重复 complete 也不会重跑失败任务。
8. 需要人工确认动作时先读取 `/technical-review` 的候选和来源指纹，再按其结构提交复核。导出使用 `/technical-review/export`。复核记录不自动覆盖机器产物，也不自动成为正式标定数据。

前端现有实现入口：[scoring-demo-web/app/lib/api-client.ts:111](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:111)、[scoring-demo-web/app/lib/resumable-upload.ts:24](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:24)。

### 任务状态与接口选择

| 任务顶层状态 | 任务详情 | 主报告与技术评估 | 轨迹与骨架预览 | 人工复核与产物 |
| --- | --- | --- | --- | --- |
| queued | 可以查询 | 409，尚未成功 | 409，不满足预览状态 | 409，尚未成功 |
| running | 可以查询，显示进度 | 409，尚未成功 | 接口允许；是否有内容取决于已有产物和证据 | 409，尚未成功 |
| succeeded | 可以查询及读取结果链接 | 可以构建；仍可能因文件/证据/版本问题报错 | 可以按限制读取 | 可以按接口约束读取或提交 |
| failed | 查询本身仍可 200，读取 error | 409 | 409 | 409 |

上表假设任务存在、已通过鉴权、请求参数与复核请求体合法、复核规则目录可用，且下载产物名合法。它说明路由的状态门槛，不承诺每个 succeeded 任务都有所有可选产物；更早执行的输入解析或目录检查可能先返回 422/503。

### 直接请求 FastAPI 的最小示例

以下是 Windows 终端 `curl.exe` 示例，需替换服务地址、文件路径、令牌和任务 ID。它们只用于文档说明，本次没有执行。

```powershell
# 发现服务。开发环境未配置 key 时，可以省略 Authorization 头。
curl.exe -H "Authorization: Bearer YOUR_API_KEY" "http://127.0.0.1:8000/v1/meta"

# 直接上传；一次文件提交形成一个任务，HTTP 202 不代表分析结束。
curl.exe -H "Authorization: Bearer YOUR_API_KEY" -F "video=@C:/videos/practice.mp4" -F "court_mode=disabled" -F "write_annotated_video=true" "http://127.0.0.1:8000/v1/jobs"

# 用上一步返回的 id 替换 JOB_ID。
curl.exe -H "Authorization: Bearer YOUR_API_KEY" "http://127.0.0.1:8000/v1/jobs/JOB_ID?report_contract=current"

# 仅在任务 succeeded 后取主报告。
curl.exe -H "Authorization: Bearer YOUR_API_KEY" "http://127.0.0.1:8000/v1/jobs/JOB_ID/demo-result?report_contract=current"

# 读取源视频 0 到 10 秒的骨架窗口；不要把毫秒误写成秒。
curl.exe -H "Authorization: Bearer YOUR_API_KEY" "http://127.0.0.1:8000/v1/jobs/JOB_ID/pose-preview?start_ms=0&duration_ms=10000&sample_limit=600"
```

不要在浏览器构建产物或公开前端环境变量中填后端共享 key。现有前端通过服务器网关注入，这是项目已经采用的连接方式；这里的终端示例用于直接调试后端。

## 阅读辅助函数与定位报错

### 不是所有 def 都是 HTTP 接口

只有被 `@app.get/post/put/...` 注册的函数才在上述路由总览中。辅助函数可以被多个路由复用，例如两个上传入口最终都调用 `_submit_video()`。函数名前的下划线是内部使用的命名约定，不会单独阻止 Python 调用，也不是 HTTP 权限控制。

| 函数 | 核心作用 | 源码 |
| --- | --- | --- |
| `_redact_public_value` | 递归脱敏公开 JSON 中的路径和网络标识 | [src/rallymate_service/api.py:122](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:122) |
| `_public_summary` | 按调用方要求选择返回原 summary 或脱敏副本 | [src/rallymate_service/api.py:151](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:151) |
| `_should_redact_public` | 判断是否生产环境或显式公开部署 | [src/rallymate_service/api.py:158](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:158) |
| `_redact_readiness_reason` | 替换诊断文字里的服务器路径 | [src/rallymate_service/api.py:162](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:162) |
| `_public_error_detail` | 按公开模式与数据类型处理错误详情 | [src/rallymate_service/api.py:168](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:168) |
| `_production_api_key_reasons` | 生成生产环境 key 就绪检查原因，不等于 authorize 的直接比较 | [src/rallymate_service/api.py:220](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:220) |
| `_public_url` | 按 public_base_url 组装 API 链接 | [src/rallymate_service/api.py:249](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:249) |
| `_safe_request_id` | 验证或生成有长度限制的请求标识 | [src/rallymate_service/api.py:255](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:255) |
| `_public_job` | 筛选数据库字段，增加产物链接并执行兼容投影 | [src/rallymate_service/api.py:263](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:263) |
| `_safe_filename` | 保留扩展名并规范化上传文件名 | [src/rallymate_service/api.py:332](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:332) |
| `_parse_polygon` | 解析四个归一化点的 JSON 字符串并检查形状/范围 | [src/rallymate_service/api.py:339](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:339) |
| `create_app` | 创建应用、依赖、缓存和路由，执行配置校验 | [src/rallymate_service/api.py:366](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:366) |
| `summary_with_actions` | 为已成功的旧动作结果补算当前版本派生视图，使用进程缓存 | [src/rallymate_service/api.py:390](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:390) |
| `lifespan` | 在应用启动时初始化任务库与人工复核库 | [src/rallymate_service/api.py:415](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:415) |
| `request_id_middleware` | 把请求标识关联到 request.state 与响应头 | [src/rallymate_service/api.py:440](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:440) |
| `authorize` | 执行共享 Bearer key 校验或公开配置缺失处理 | [src/rallymate_service/api.py:458](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:458) |
| `_submit_video` | 复用两个上传入口的选项校验、视频保存、探测与任务入库 | [src/rallymate_service/api.py:780](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:780) |
| `technical_review_job` | 确保任务存在且已成功，供人工复核路由复用 | [src/rallymate_service/api.py:1057](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1057) |

### 遇到一个报错 应从哪里查

| 看到的现象 | 优先检查 | 原因 |
| --- | --- | --- |
| 页面返回 404 `route_not_allowed` | 网关白名单与 HTTP 方法 | 请求可能没有到达 FastAPI |
| 401 `missing or invalid bearer token` | 请求是否经过预期网关、后端是否收到正确 Bearer | 属于鉴权依赖，不是视频分析失败 |
| 503 `authentication_not_configured` | 服务公开模式与 key 配置 | 后端拒绝在缺少认证配置时开放任务数据 |
| `/health/ready` 是 200 但 `not_ready` | 响应 `reasons` | 该接口以 JSON 状态表达就绪结果 |
| 202 后长期 queued | Worker 入口、队列消费、现有 running 任务 | API 接受任务与 Worker 执行独立 |
| 200 但 job.status=failed | 任务 error 和 Worker 日志 | HTTP 查询成功，不代表任务成功 |
| 409 `job has not succeeded` | 是否过早读取成功任务接口 | 先轮询到 succeeded，再请求完整报告 |
| 422 参数错误 | 参数位置、类型、范围和请求体格式 | multipart、JSON 对象、二进制分块不能混用 |
| 分片422或409 | chunk_size、索引、字节数、块摘要、已保存内容 | 传输协议不是“随便切一块发过去” |
| 目录或证据来源冲突 | 返回的具体 code、注册表版本、来源指纹 | 重复请求不一定能修复版本不一致 |
| 前端显示字段缺失 | report_contract、任务状态、证据可用性 | 部分字段条件性出现，旧投影也可能省略新版结构 |
| JSON 的路径被替换，下载文件里仍有路径 | 响应视图与原始产物区别 | 原始产物默认按原字节返回 |

对于自定义异常，从三个位置阅读最有效：`class` 看错误类别，`raise` 看触发条件与具体文案，`except` 看调用方如何处理。一般无需继续深入 Python 的 BaseException 实现，除非你研究的是异常对象自身的语言机制。

### 保留在当前实现中的边界

这些内容来自本次源码核对，列在一起便于联调时避免误用，不表示本次修改了接口。

- `dict` 类型提示无法完整约束嵌套 JSON；部分业务必填字段缺失时返回的代码不一定是 422，详见上传创建接口。
- `/health/ready` 检查配置和文件状态，不能替代 Worker 心跳或真实推理验证。
- `court_mode` 等参数仍接受并保存，但当前默认视频 pipeline 固定关闭场地定位；参数存在不能被宣传为功能已经启用。
- 元数据的 4 MiB 分片值是上限/历史兼容值，新上传会话应遵循其返回的 128 KiB `chunk_size`。
- 就绪检查 GET 可能初始化数据库；结果类 GET 会计算派生视图并更新进程内缓存，但不因此重写任务的机器分析产物。不能仅凭 HTTP 方法推断其计算和存储行为，具体副作用见各接口章节。
- 原始产物默认 `current`，主要报告查询默认 `legacy-v1`；不同响应形态不必然是同一份数据不一致。
- 当前没有任务删除、取消、用户注册登录、通用任务重试或人工修改现有任务参数路由。
- `202`、`succeeded`、候选动作、证据就绪参考值、正式教练评分是不同层次的状态与语义，前端应按各自字段解释。

## 源码索引与文档核验

| 文件 | 本次快照 SHA256 前 12 位 | 阅读内容 |
| --- | --- | --- |
| [scoring-demo-web/app/lib/api-client.ts:1](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/api-client.ts:1) | `1eda5462e8c3` | 浏览器 API 客户端 |
| [scoring-demo-web/app/lib/resumable-upload.ts:1](C:/Users/Admin/Documents/网球/scoring-demo-web/app/lib/resumable-upload.ts:1) | `5a99ba0a5883` | 浏览器续传流程 |
| [scoring-demo-web/worker/gateway.ts:1](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/gateway.ts:1) | `247d9449a84d` | 前端网关白名单与认证注入 |
| [scoring-demo-web/worker/index.ts:1](C:/Users/Admin/Documents/网球/scoring-demo-web/worker/index.ts:1) | `6d7f689c60ef` | 关联业务实现 |
| [src/rallymate_scoring/data/technical_review_rules.json:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/data/technical_review_rules.json:1) | `ec1d66b76322` | 关联业务实现 |
| [src/rallymate_scoring/granularity.py:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/granularity.py:1) | `6981059f6968` | 关联业务实现 |
| [src/rallymate_scoring/loop.py:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/loop.py:1) | `22b79515340b` | 关联业务实现 |
| [src/rallymate_scoring/report.py:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/report.py:1) | `2f6f76e69f61` | 关联业务实现 |
| [src/rallymate_scoring/source_assessment.py:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/source_assessment.py:1) | `61896f73b9b9` | 关联业务实现 |
| [src/rallymate_scoring/stroke_candidates.py:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/stroke_candidates.py:1) | `6cf0f08bebc4` | 关联业务实现 |
| [src/rallymate_scoring/technique_assessment.py:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_assessment.py:1) | `ac1f07a9679b` | 技术证据评估 |
| [src/rallymate_scoring/technique_registry.py:1](C:/Users/Admin/Documents/网球/src/rallymate_scoring/technique_registry.py:1) | `b037514635e6` | 技术动作目录及异常分类 |
| [src/rallymate_service/analysis_report.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/analysis_report.py:1) | `c4095e143ccf` | 关联业务实现 |
| [src/rallymate_service/api.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:1) | `15cfb21fe928` | 路由、鉴权、公开响应与输入参数 |
| [src/rallymate_service/cli.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/cli.py:1) | `119232aeb8a3` | 关联业务实现 |
| [src/rallymate_service/config.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/config.py:1) | `177bcd48cc02` | 服务配置、默认值及公开部署检查 |
| [src/rallymate_service/database.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/database.py:1) | `edc4028bce49` | 任务数据库、队列、进度及租约 |
| [src/rallymate_service/footwork_review.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/footwork_review.py:1) | `3d40eec7b2a3` | 关联业务实现 |
| [src/rallymate_service/report_compatibility.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/report_compatibility.py:1) | `b489cd155e0e` | 报告契约兼容投影 |
| [src/rallymate_service/source_assessment.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/source_assessment.py:1) | `fe9b626cead1` | 关联业务实现 |
| [src/rallymate_service/technical_review.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/technical_review.py:1) | `cae8c49536ae` | 人工复核输入、绑定、历史与导出 |
| [src/rallymate_service/training_evaluation.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:1) | `ba29ead2936b` | 关联业务实现 |
| [src/rallymate_service/uploads.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/uploads.py:1) | `cd03a5ad4460` | 上传清单、分块持久化、合并和续传 |
| [src/rallymate_service/user_demo.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/user_demo.py:1) | `f39c28d981ee` | 面向展示的综合结果构造 |
| [src/rallymate_service/worker.py:1](C:/Users/Admin/Documents/网球/src/rallymate_service/worker.py:1) | `4294ce655da8` | 任务执行与结果状态 |
| [src/rallymate_vision/pipeline.py:1](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:1) | `be8b581e3a8a` | 关联业务实现 |
| [src/rallymate_vision/pose_playback.py:1](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose_playback.py:1) | `716a6a94811a` | 骨架窗口和主球员选择 |
| [src/rallymate_vision/trajectory.py:1](C:/Users/Admin/Documents/网球/src/rallymate_vision/trajectory.py:1) | `10b9f1dd992f` | 球与球拍观测窗口及外推 |
| [tests/test_motion_api.py:1](C:/Users/Admin/Documents/网球/tests/test_motion_api.py:1) | `3808aa63f4d0` | 相关测试依据 |
| [tests/test_pose_playback.py:1](C:/Users/Admin/Documents/网球/tests/test_pose_playback.py:1) | `85e96d9fe08f` | 相关测试依据 |
| [tests/test_report_compatibility.py:1](C:/Users/Admin/Documents/网球/tests/test_report_compatibility.py:1) | `242184920ba7` | 相关测试依据 |
| [tests/test_resumable_uploads.py:1](C:/Users/Admin/Documents/网球/tests/test_resumable_uploads.py:1) | `0f810330a92e` | 相关测试依据 |
| [tests/test_service.py:1](C:/Users/Admin/Documents/网球/tests/test_service.py:1) | `6110632a36e1` | 相关测试依据 |
| [tests/test_service_boundaries.py:1](C:/Users/Admin/Documents/网球/tests/test_service_boundaries.py:1) | `eb096b467e9b` | 相关测试依据 |
| [tests/test_technical_review.py:1](C:/Users/Admin/Documents/网球/tests/test_technical_review.py:1) | `271049cdb4d4` | 相关测试依据 |
| [tests/test_technique_assessment.py:1](C:/Users/Admin/Documents/网球/tests/test_technique_assessment.py:1) | `279661c8fa5c` | 相关测试依据 |
| [tests/test_trajectory_api.py:1](C:/Users/Admin/Documents/网球/tests/test_trajectory_api.py:1) | `c36862eafce9` | 相关测试依据 |

`api.py` 本次完整 SHA256：`15cfb21fe928262ef1bd36b226cf4cc3502b2e59b70addfbf338174d0dddf1a8`。

核验方式：静态解析 `api.py` 中的路由装饰器，确认每个 HTTP 方法与路径都在本文有独立章节；核对每个路由的函数签名和参数；阅读业务模块及相关现有测试，区分“测试文件写了什么”与“本次实际运行了什么”；验证文档中的源码链接、JSON 示例语法及 HTML 目录。此次是文档任务，没有启动 API、执行视频推理、修改数据库或跑完整测试套件。

本文反映生成时的源码快照。以后新增路由、改动默认值或升级响应结构时，应同时更新相应章节；旧的行号可能随文件编辑发生移动。HTML 中的源码摘录也是本次快照，用于辅助阅读，不替代后来修改后的源文件。
