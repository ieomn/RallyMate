# RallyMate 网球训练空间

深色运动科技界面，主流程为训练总览 → 上传视频 → 处理进度 → 回放与测量 → 导出报告。训练记录支持搜索和排序，拍摄指南包含可勾选清单。主产品不展示模拟分数；骨架和球路默认关闭，按需从播放器的“分析工具”开启。

页面地址使用 `?view=overview|sessions|analysis|guide`，旧 `?job=...` 报告链接继续有效。首页不会因为浏览器保留旧任务编号而自动跳回报告。记录来自当前浏览器，日期表示最近查看时间，不冒充视频拍摄时间。改版范围与验收见 [前端产品改版说明](../docs/前端产品改版说明_2026-10-04.md)。

品牌宣传页位于 `/welcome`，包含原创网球主视觉、可拖动的复盘交互示意、三步流程与常见问题，入口连接实际训练空间。设计参考、素材生成提示词与验收记录见 [宣传页设计与素材](../docs/宣传页设计与素材_2026-10-04.md)。

## 本机启动

先从仓库根目录启动 Python 分析服务，详情见根 README，再运行 `npm ci`、`npm run dev`。浏览器访问终端打印的地址（默认 localhost:3000）。Vite 将 /v1 与 /health 转发到本机 8000 端口。

在本目录新建 `.dev.vars`（已被 Git 忽略），仅放服务端配置：

```dotenv
RALLYMATE_API_ORIGIN=http://127.0.0.1:8000
RALLYMATE_API_KEY=
MIMO_ADVICE_ENABLED=0
MIMO_API_KEY=
MIMO_PROVIDER=openai
MIMO_MODEL=mimo-v2.5-pro
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
```

填入分析服务配置后重启开发服务。Vite 上传代理与 Worker 建议服务共用 `.dev.vars` 中的分析地址和密钥；进程环境变量可覆盖代理设置。不要把这些值放到任何公开变量中。

MiMo 默认不调用；识别依据与补充拍摄建议由服务端生成。真实调用必须显式设置 `MIMO_ADVICE_ENABLED=1` 并使用官方按量 API 密钥。Anthropic 兼容模式使用 `MIMO_PROVIDER=anthropic`、`MIMO_BASE_URL=https://api.xiaomimimo.com/anthropic`。接口地址仅接受这两个精确的官方配置，不接受自定义反代地址、Token Plan 密钥或套餐端点。

[小米接入文档](https://mimo.mi.com/docs/en-US/quick-start/summary/first-api-call)区分了按量 API 与套餐凭据；[Token Plan 规则](https://mimo.mi.com/docs/zh-CN/tokenplan/Token%20Plan/subscription)限制自定义应用后端等非编程用途。切换反代不能改变套餐使用范围。公网付费调用启用前，须完成[反代与额度方案](../deploy/autodl/MIMO_PROXY_PLAN.md)中的匿名任务授权、持久配额和预算保护；这些保护目前仍是实施计划，不要仅填写新密钥就对公网启用。

## 访问与建议防护

- `/api/advice` 接收主题、水平、目标、最多 600 字问题与可选 jobId。
- 从固定分析服务读取并核对 jobId，只使用所选动作类别的证据；步伐结果不能支持底线或接发评价。
- 无视频、分析读取失败、该类动作证据不足时直接返回明确说明与补证步骤，不调用 MiMo，不猜动作错误原因。
- 只有二维运动时明确标注规则推断、触球未确认及缺失阶段；部分片段不能拼成完整动作。模型关闭或异常与识别不足分别展示。
- 有可用证据时只发送压缩后的该类事实和问题，不向 MiMo 发送视频、帧或模型文件。
- 每实例并发上限 2、每来源 10 分钟 5 次；请求 8 KB、模型响应 64 KB、超时 25 秒。
- 模型只选择已有事实与批准建议的编号；展示文字由服务端生成，拒绝自由诊断、编造事实或新增训练方法。
- 输入注入、未知字段、异常输出与身体不适均有独立处理。进程限流不能代替云端 WAF 或多实例持久配额。

## 云端 Worker

在 Worker 的服务端 Secrets 中配置 `RALLYMATE_API_ORIGIN`（可达的 HTTPS 分析服务）、`RALLYMATE_API_KEY`。MiMo 保持默认关闭，启用条件见上文。网站访问无需账号或密码；分析服务与模型密钥仍仅保存在服务端。独立部署的 Worker 不得把 localhost 配成云端分析地址。

Worker 仅代理明确列出的任务、结果、媒体与健康路径；隔离浏览器 Cookie/Authorization，支持媒体 Range、超时和明确错误。持有网站链接即可上传、推理及查看对应任务结果。自行托管的 Nginx 方案见 `../deploy/README.md`。

## 验证

```bash
npm test
npm run typecheck
npm run lint
```

测试覆盖长任务恢复、断网重试、错任务结果、回放 Range、免登录访问、服务端 API 鉴权、代理路径与来源限制、提示注入、响应约束、限流与 SSR 初始结果列表。浏览器扩展自动选文件需要启用“允许访问文件网址”；这不影响用户自己通过网页文件选择器上传。

## 本机生产模式验收

```bash
npm run build
node --env-file=.dev.vars node_modules/vinext/dist/cli.js start --port 3001 --hostname 127.0.0.1
```

此模式直接运行构建产物，通过 Worker 的代理读取分析服务，不依赖 Vite 开发代理。线上使用 Node.js 22.13 或更高版本，并由进程管理器注入 Secrets，不能携带本机 `.dev.vars` 发布。

若前端、Python 分析服务与 `cloudflared` 同在 AutoDL 等服务器上，配置 `RALLYMATE_API_ORIGIN=http://127.0.0.1:8000`、`RALLYMATE_LOCAL_TUNNEL=1`，使 Cloudflare HTTPS 入口能通过前端访问回环分析服务。保留 `RALLYMATE_API_KEY` 与后端配置一致。启动前端后运行 `cloudflared tunnel --url http://127.0.0.1:3001`，浏览器通过隧道地址免登录访问；上传请求仍限定同源。
