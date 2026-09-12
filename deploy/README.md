# AutoDL + 独立域名部署手册

这份部署文件把网页和推理服务拆成两个可独立发布的单元：

```text
浏览器 / app.example.com
        │  HTTPS（短期会话或反向代理注入 Bearer）
        ▼
RallyMate API（api.example.com，FastAPI）
        │  SQLite/文件队列
        ▼
RallyMate Worker（同一 AutoDL GPU 节点，持久化模型进程）
```

网页不需要知道 Worker 的路径或模型位置，只使用 `/v1/meta`、`/v1/jobs`、
`/v1/techniques`、`/v1/jobs/{id}/trajectory` 和
`/v1/jobs/{id}/technique-assessment`。没有提供真实域名时，把下面的
`app.example.com` 和 `api.example.com` 换成自己的域名即可。

## 1. 在 AutoDL 准备目录

在仓库根目录执行（目录由部署者创建，不能让浏览器请求选择路径）：

```bash
mkdir -p service_data models calibration config
chown -R 10001:10001 service_data
chmod 750 service_data
```

把检测和 Pose 权重放到 `models/`。`deploy/Dockerfile` 使用 CUDA 12.8 + Ubuntu 22.04/Python 3.10，
会安装代码和运行时，
但不会把大体积权重复制进镜像；`models/` 以只读方式挂载到容器。

`calibration/` 仅用于已经通过独立测试和可信账本审核的标定资产，也以只读方式挂载。
没有正式标定资产时保持为空，服务会继续提供证据就绪度和兼容 Demo，
不会冒充 A—E 技术等级。

## 2. 配置环境变量

复制示例并生成随机密钥：

```bash
cp deploy/.env.example .env
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

将输出写入 `.env` 的 `RALLYMATE_API_KEY`。生产至少修改：

```text
RALLYMATE_ENVIRONMENT=production
RALLYMATE_MODEL_LICENSE_ACK=enterprise   # 或经过法律审查的其它选项
RALLYMATE_API_KEY=<32 字符以上随机值>
RALLYMATE_PUBLIC_BASE_URL=https://app.example.com
RALLYMATE_CORS_ORIGINS=https://app.example.com
RALLYMATE_DEVICE=0
```

上面的 `RALLYMATE_PUBLIC_BASE_URL=https://app.example.com` 对应推荐的同源网关，
使任务返回链接继续经过 app 域名的服务端鉴权注入；如果你明确采用独立 API 域名，
才改成 `https://api.example.com`，并同时部署服务端会话/短期 token 代理。
`RALLYMATE_PUBLIC_BASE_URL` 只接受 `http(s)://host[:port]`，不带路径、查询串或凭据。
`RALLYMATE_CORS_ORIGINS` 必须是显式来源，生产环境不能使用 `*`。

Docker 镜像会把生命周期清单、当前 `runtime_feasibility` 注册表以及清单引用的
历史/规划工件复制到 `/app/`，因此完整生命周期审计仍可在容器内复现。服务运行时
使用的以下两个路径应保持可读且不可由任务请求覆盖：

```text
RALLYMATE_SCORING_REGISTRY_LIFECYCLE_MANIFEST=/app/registry-lifecycle.json
RALLYMATE_SCORING_FEASIBILITY_REGISTRY=/app/metric-feasibility-pose-wave-v2.json
```

如果改用独立 wheel/源码安装而不是本 Dockerfile，必须把这两份运维权威文件以只读
挂载提供，并显式设置这两个变量；不要依赖 site-packages 中不存在的仓库根目录。

### 可选：审计版技术注册表

默认使用随包的 `src/rallymate_scoring/data/technique_metrics.json`。如果要发布
经审核的替代版本：

1. 将文件放在宿主机 `config/technique-metrics-audited.json`；
2. 在 `deploy/docker-compose.yml` 的 `api` 和 `worker` 两个服务中取消下面这一行的注释：

   ```yaml
   - ../config/technique-metrics-audited.json:/app/technique-metrics-audited.json:ro
   ```

3. 设置 `RALLYMATE_TECHNIQUE_REGISTRY=/app/technique-metrics-audited.json`。

服务启动时会校验 JSON、版本和 SHA-256；任务提交时会把注册表快照写入请求，
若排队期间文件被替换，结果接口会返回 409，而不是静默混用两个版本。

## 3. 启动 API 和 GPU Worker

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
curl --fail http://127.0.0.1:8000/health/live
curl --fail -H "Authorization: Bearer ${RALLYMATE_API_KEY}" \
  http://127.0.0.1:8000/health/ready
```

`/health/live` 只表示进程存活；`/health/ready` 会检查模型、许可证、注册表和标定资产，
生产密钥缺失或仍是占位符时明确返回 `not_ready`。查看日志：

```bash
docker compose -f deploy/docker-compose.yml logs -f api worker
```

更新时先 `docker compose pull`（如使用私有镜像）或重新 `--build`，再滚动重启。
`service_data/`、`models/`、`calibration/` 不随镜像删除，便于回滚和保留任务证据。

## 4. 绑定域名和 HTTPS

推荐让 Nginx/Caddy 监听 443，把 API 仅暴露在本机或私网。Nginx 示例见
[`nginx-rallymate.conf.example`](nginx-rallymate.conf.example)，其中已经设置：

- `client_max_body_size` 与 API 上传上限一致；
- `/v1/`、`/health/` 的长请求超时；
- `X-Forwarded-*` 和 `X-Request-ID` 透传；
- 不把 Worker 文件目录映射到静态站点。

推广网页建议走同源网关：先把
[`nginx-rallymate-api-auth.conf.example`](nginx-rallymate-api-auth.conf.example)
复制为 `/etc/nginx/snippets/rallymate-api-auth.conf`，替换为 API 的 Bearer key，
并将文件权限设为 `600`。示例中的 `app.example.com` `/v1/` 与 `/health/`
location 会把请求转发到本机 API，并由 Nginx 注入 header；浏览器因此不需要、
也不应持有长期 key。若网页直接请求 `api.example.com`，则必须由另一个服务端
会话/短期 token 层完成同样的认证注入，不能只把 `VITE_RALLYMATE_API_URL`
指向受保护 API。

证书、DNS、WAF、速率限制和访问日志由域名侧负责。API 自己仍会校验 Bearer key，
不要把长期 key 放入公开的 `NEXT_PUBLIC_*`/`VITE_*` bundle；由同源网关、短期会话
或服务端代理完成认证。上传接口建议再配置 WAF/租户级限额。

## 5. 发布网页

在 `scoring-demo-web/` 构建推广级前端，并用 vinext Node 服务承载页面：

```bash
npm ci
# 同源网关模式：留空，浏览器请求 app.example.com/v1/*
VITE_RALLYMATE_API_URL= npm run build
PORT=3000 HOST=127.0.0.1 npm run start
```

当前 App Router 构建包含 `/api/scorecard` 兼容 route handler，`dist/client/` 是浏览器
资源目录而不是含 `index.html` 的独立站点；Nginx 示例因此把 `app.example.com` 反代到
`vinext start`（Node 端口 3000）。不要把 `dist/server/` 直接当作静态根目录。若以后
移除 route handler 并显式启用、验收 `output: "export"`，才可以把经验证的静态导出目录
交给 CDN/Nginx。

也可以在 Next/vinext 主机设置 `NEXT_PUBLIC_RALLYMATE_API_URL`。同源网关模式下，
把 `RALLYMATE_PUBLIC_BASE_URL` 设为 `https://app.example.com`，这样任务返回的
轨迹、评估和 artifact 链接会继续走已注入认证的 `/v1/` location。若选择独立
API 域名模式，网页端必须配套服务端代理或短期会话。生产不要依赖 Vite 的本地
`/v1` 代理；它只用于开发。网页启动后应能访问：

```text
# 同源网关模式
GET https://app.example.com/v1/meta
GET https://app.example.com/v1/techniques
# 独立 API 域名模式（由服务端会话/短期 token 注入认证）
GET https://api.example.com/v1/meta
GET https://api.example.com/v1/techniques
```

前端会显示兼容规则库版本和 24 项技术目录版本；版本不一致时以 API 返回为准。

## 6. 运行语义和验收清单

- 球轨迹是 `frames.jsonl` 检测中心加最多 5 秒的短时常速度外推，最多 8 个预览点；
  预览解析对 artifact 设有 256 MiB、200 万行和 50 万观测点上限，超限会明确失败，
  不会把异常 JSONL 无界加载进内存；
  `predicted_covered_horizon_ms` 标明实际覆盖时长，整体标记为 `heuristic_preview`，不是物理预测或准确率。
- 球拍当前是通用 `bbox_only` 检测，`association_status=unassociated`；没有拍头、
  拍柄、甜区关键点或目标球员绑定时，前端必须显示“待确认”。
- 新五份 Word 定义共 24 项技术，接口只返回证据就绪度；缺失证据为
  `unavailable`/`partial`，不补零分、不推断 A—E。
- `/v1/jobs/{id}`、`/v1/jobs/{id}/trajectory`、`/v1/jobs/{id}/technique-assessment`
  都带任务和注册表版本信息，适合作为前端轮询和审计入口。

上线前逐项确认：

1. `health/ready` 为 `ready`，且没有 placeholder key 警告；
2. AutoDL GPU 可见，Worker 日志显示正确 Pose preset；
3. 从网页上传一段受支持的 MP4/MOV/M4V/AVI/MKV，任务能从 `queued` 到 `succeeded`；
4. 轨迹和技术评估接口返回 200，缺失证据仍保持明确的不可用状态；
5. 浏览器 Origin、上传大小、HTTPS、日志脱敏和数据保留策略已经由部署者审核。

完整字段和证据边界见 [`docs/TECHNIQUE_METRICS_CONTRACT.md`](../docs/TECHNIQUE_METRICS_CONTRACT.md)。
