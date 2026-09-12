# RallyMate Score Lab

可运行的 GS 底线击球 + FS 步伐事件评分系统 Demo。

## Demo 包含

- 从原有指标卡抽取的 298 条规则：GS 248 条，FS 50 条；另接入五份最新技术定义的 24 项动作目录。
- 完整验收演示：298 项全部经过同一套证据门禁、四维评分、A—E 分级和模块聚合逻辑。
- 真实数据审慎模式：内置四组一期 FULL-TEST 摘要，也可在页面导入新的 `summary.json`。
- 可追溯报告：每项展示依赖、计算方式、等级原文和 AI 教练反馈，并可导出 JSON。
- HTTP 接口：`GET /api/scorecard` 查看能力；`POST /api/scorecard` 生成报告。
- 前端 API client：可通过公开环境变量切换同源 API、本地 Python 服务或 AutoDL/域名服务，不在代码中写死地址。
- 视频上传完成后会读取 `/v1/jobs/{id}/trajectory` 与 `/v1/jobs/{id}/technique-assessment`，把球观测、短时启发式外推、球拍 bbox 和证据就绪度回填到统一结果区。

## 评分边界

完整演示模式使用可复现的合成特征，目的是验收评分系统，不代表真实球员表现。真实一期数据缺少事件切分和标定后的技术特征，所以只给证据就绪度，不生成技术等级。

## 本地运行

```bash
npm install
npm run dev
```

本地 Vite 开发服务器默认把 `/v1/*` 和 `/health/*` 代理到
`http://127.0.0.1:8000`；可用 `RALLYMATE_API_PROXY` 覆盖。生产静态站点请设置
`VITE_RALLYMATE_API_URL` 或 `NEXT_PUBLIC_RALLYMATE_API_URL`，不要依赖开发代理。

构建与验收：

```bash
npm test
```

## 接入视频服务

浏览器端请求由 `app/lib/api-client.ts` 统一封装。默认使用同源
`/api/scorecard` 与 `/v1/jobs`；部署到远程服务时设置以下变量（`NEXT_PUBLIC_*`
或对应的 `VITE_*`）：

```text
NEXT_PUBLIC_RALLYMATE_API_URL=https://your-api.example.com
NEXT_PUBLIC_RALLYMATE_SCORECARD_PATH=/api/scorecard
NEXT_PUBLIC_RALLYMATE_JOBS_PATH=/v1/jobs
```

任务接口应返回 `POST /v1/jobs` 的 `{ id, status }`，并提供
`GET /v1/jobs/{id}`（状态、`progress`、`stage`、错误信息）和
`GET /v1/jobs/{id}/demo-result`（结果或 `summary`）。评分接口的结果字段是
`overallScore`、`overallGrade`、`overallEvidence`、`acceptanceStatus`、`domains`、
`results[]`；每项结果至少包含 `indicatorId`、`status`、`score`（不可评分时为
`null`）、`grade`、`evidence`、`confidence`、`dimensions`、`verdict` 和
`feedback`。真实数据不足时请保持 `score`/`grade` 为 `null`，由前端展示证据审计状态。

结果页的可选增强字段建议放在 `demo-result` 中：`artifacts.evidence_frames[]`
（`frame_url`、`timestamp_ms`、`labels`、`source`）、`features.ball.trajectory`
（点序列、预测方向、`confidence`）、`signals.racket`（识别状态、关键点、`confidence`）
和 `signals.grip`（候选状态、`confidence`、`status`）。每个来源应携带
`source`/`is_demo`；缺失或未确认的观测返回 `status: "unavailable"`，前端会保留为待确认，
不会把占位视觉当成模型结果。

API 开启 Bearer key 时，浏览器不应把长期密钥编译到公开环境变量；生产请在同源
反向代理或短期会话层完成认证。仓库中的 Nginx 示例把 app 域名的 `/v1/*` 请求
转发到私有 API 并在服务端注入 Bearer；此模式下 `VITE_RALLYMATE_API_URL` 留空。
若直接把它指向受保护的独立 API 域名，浏览器不会自动携带长期 key，上传会被拒绝。
前端页面会把 API 返回的错误 `detail.code/message_zh` 原样转成可读提示。

`/api/scorecard` 是本地兼容验收适配器，保留旧版 298 条 Demo 规则；远程 FastAPI
生产服务的权威入口是 `/v1/meta`、`/v1/techniques`、`/v1/jobs` 及其轨迹/技术评估子路由。
不要把浏览器里的兼容 Demo 分数当作远程任务的真实技术评分。

规则注册表由 `scripts/extract_metric_cards.py` 从源 Word 文件生成到 `app/data/metric-cards.json`。
