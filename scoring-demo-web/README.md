# RallyMate 网球动作工作台

保留评分总览、动作列表与右侧详情、证据回放和报告导入导出。主流程为上传 → 识别 → 评分 → 建议。历史示例与真实视频分开显示。

## 本机启动

先从仓库根目录启动 Python 分析服务，详情见根 README，再运行 `npm ci`、`npm run dev`。浏览器访问终端打印的地址（默认 localhost:3000）。Vite 将 /v1 与 /health 转发到本机 8000 端口。

在本目录新建 `.dev.vars`（已被 Git 忽略），仅放服务端配置：

```dotenv
RALLYMATE_API_ORIGIN=http://127.0.0.1:8000
RALLYMATE_API_KEY=
MIMO_API_KEY=
MIMO_PROVIDER=openai
MIMO_MODEL=mimo-v2.5-pro
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
```

填入自己的密钥后重启开发服务。Vite 上传代理与 Worker 建议服务共用 `.dev.vars` 中的分析地址和密钥；进程环境变量可覆盖代理设置。不要把这些值放到任何公开变量中。

MiMo 的 Anthropic 兼容模式将 `MIMO_PROVIDER` 改为 `anthropic`，`MIMO_BASE_URL` 改为 `https://token-plan-cn.xiaomimimo.com/anthropic`。已按[小米官方接入文档](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call)配置接口；本次真实验证使用 OpenAI 兼容模式。

## 访问与建议防护

- `/api/advice` 接收主题、水平、目标、最多 600 字问题与可选 jobId。
- 有结果时从固定分析服务读取并核对 jobId；没有结果时仍可给通用提示，不把用户描述冒充检测结论。
- 只发送压缩动作摘要，不向 MiMo 发送视频、帧或模型文件。
- 每实例并发上限 2、每来源 10 分钟 5 次；请求 8 KB、模型响应 64 KB、超时 25 秒。
- 模型只返回短摘要和批准提示的编号；实际步骤、时长和安全提醒由服务端生成。
- 输入注入、未知字段、异常输出与身体不适均有独立处理。进程限流不能代替云端 WAF 或多实例持久配额。

## 云端 Worker

在现有 Sites 的服务端 Secrets 中配置 `RALLYMATE_API_ORIGIN`（可达的 HTTPS 分析服务）、`RALLYMATE_API_KEY`、`MIMO_API_KEY`，并设置私人入口账号 `RALLYMATE_WEB_USER` 与至少 16 字符的 `RALLYMATE_WEB_PASSWORD`。缺少私人访问配置会返回 503，不会匿名开放共享密钥。不得把 localhost 配成云端分析地址。

Worker 仅代理明确列出的任务、结果、媒体与健康路径；隔离浏览器 Cookie/Authorization，支持媒体 Range、超时和明确错误。自行托管的 Nginx 方案见 `../deploy/README.md`。多人公开产品需要账户、任务归属及持久化配额后再开放。

## 验证

```bash
npm test
npm run typecheck
npm run lint
```

测试覆盖长任务恢复、断网重试、错任务结果、回放 Range、代理访问控制、提示注入、响应约束、限流与 SSR 初始结果列表。浏览器扩展自动选文件需要启用“允许访问文件网址”；这不影响用户自己通过网页文件选择器上传。

## 本机生产模式验收

```bash
npm run build
node --env-file=.dev.vars node_modules/vinext/dist/cli.js start --port 3001 --hostname 127.0.0.1
```

此模式直接运行构建产物，通过 Worker 的代理读取分析服务，不依赖 Vite 开发代理。线上应使用进程管理器注入 Secrets 并配置私人入口账号，不能携带本机 `.dev.vars` 发布。
