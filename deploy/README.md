# RallyMate 本地测试与 Linux 上线部署

本版本保留 React / Vinext、FastAPI 和独立 GPU Worker，提供邮箱注册、可撤销登录会话、个人历史和管理员后台。生产主库使用 PostgreSQL；视频、报告与模型继续使用现有文件存储。本地开发仍支持 SQLite。

```text
浏览器 → HTTPS Nginx → Vinext 页面 / FastAPI 业务与账户接口
                                    ↓
                             PostgreSQL 主库
                                    ↑
                            独立 GPU Worker
                                    ↓
                          原视频、报告与回放文件
```

业务接口使用当前用户的 HttpOnly Cookie 或用户 Bearer JWT。共享 `RALLYMATE_API_KEY` 只用于运维接口，无法代替用户登录。Cookie 写操作同时校验 Origin 和 CSRF token，生产 Cookie 开启 Secure。

## 1. 本地开发与测试

在现有已通过推理测试的 Python 环境中安装新增服务依赖，保留原有 Torch、CUDA 和姿态后端配置。新环境先按对应模型文档安装推理运行时，再安装项目。

```bash
python -m pip install -e '.[service,dev]'
cd scoring-demo-web
npm ci
cd ..
```

根目录 [.env.example](../.env.example) 列出 API 配置。API 从进程环境读取变量，复制文件不会自动加载。以下为 Bash 示例；PowerShell 使用 `$env:变量名='值'` 设置同名变量。使用已有数据库前先备份，独立测试可指定新目录。

```bash
export RALLYMATE_ENVIRONMENT=development
export RALLYMATE_MODEL_LICENSE_ACK=development
unset DATABASE_URL RALLYMATE_DATABASE_URL RALLYMATE_DATABASE_PATH
export RALLYMATE_DATA_ROOT="$PWD/service_data_auth_local"
export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export PUBLIC_ORIGIN=http://localhost:3000
export AUTH_COOKIE_SECURE=false
export ADMIN_EMAIL=admin@example.com
read -rsp '首次管理员密码（至少12位，含字母和数字）: ' ADMIN_PASSWORD; echo
export ADMIN_PASSWORD
# 保留并导出当前有效的模型路径、pose backend 和 device 配置。
rallymate-dev --host 127.0.0.1 --port 8000
```

仅验证账号和页面时加 `--api-only`，不加载 GPU Worker。在第二个终端启动网页；浏览器地址与 `PUBLIC_ORIGIN` 的主机名和端口保持一致。

```bash
cd scoring-demo-web
RALLYMATE_API_ORIGIN=http://127.0.0.1:8000 npm run dev -- --hostname 127.0.0.1 --port 3000
```

访问 [本机网站](http://localhost:3000/) 进入欢迎页。单独登录后回到欢迎页。访问 [管理页面](http://localhost:3000/admin) 并登录则返回管理页面。注册成功会创建普通 `USER / APPROVED` 账户并登录。用户直接注册后进入训练总览，从训练或报告入口注册则返回原目标。浏览器旧公共缓存保留，新账户页面不读取这些公共键。下面的测试命令从仓库根目录执行。

```bash
PYTHONPATH=src:tests python -m pytest tests/test_account_migrations.py tests/test_accounts.py tests/test_auth_api.py tests/test_auth_runtime.py tests/test_auth_boundaries.py tests/test_ownership.py tests/test_account_maintenance.py tests/test_observability.py -q
PYTHONPATH=src:tests python -m pytest tests/test_service.py tests/test_resumable_uploads.py tests/test_job_cancellation.py -q
cd scoring-demo-web
npm run typecheck
npm test
```

PowerShell 先设置 `$env:PYTHONPATH='src;tests'`，再执行同一条 `python -m pytest` 命令；Linux 的路径分隔符为冒号，Windows 为分号。PostgreSQL 测试额外读取 `RALLYMATE_TEST_POSTGRES_URL`，使用独立测试数据库，不能指向生产库。测试会创建和清理测试表。本次验收结果见 [账户系统交付说明](../docs/RallyMate账户系统交付与验收.md)。

## 2. Linux 主机与模型准备

主机安装 Docker Engine、Compose v2、NVIDIA 驱动和 NVIDIA Container Toolkit。先确认 `nvidia-smi` 能列出设备，再验证容器可访问所选 GPU。Worker 默认一个副本，`RALLYMATE_GPU_DEVICE_ID` 选择物理卡，容器内使用逻辑设备 `0`；本次没有改变现有多卡调度算法。

核心服务为 `postgres`、`migrate`、`api`、`frontend`、`nginx`、`worker`、`adminer`。PostgreSQL、API、Worker 不映射宿主公开端口；Nginx 提供 80/443；Adminer 固定为 `127.0.0.1:8080:8080`。

上传数据目录必须位于支持 `renameat2(RENAME_NOREPLACE)` 和目录 `fsync` 的 Linux 文件系统，例如本地 ext4 或 XFS；上传发布依赖这些操作保证原子性。`RALLYMATE_DATA_DIR` 应指向这种文件系统上的持久目录。Docker Desktop 将 Windows 目录绑定进 Linux 容器时，本次隔离验收曾返回 `renameat2: EINVAL`；该环境应使用 Linux 虚拟机中的目录，或用原生 Linux Docker 卷覆盖数据挂载。Windows 原生开发服务使用 Windows 文件操作，不受这个 Linux 容器挂载限制影响。

在项目根目录准备挂载目录。数据目录已有文件时，先确认用途与所有者，再调整权限。

```bash
mkdir -p service_data models calibration runtime/sam3d
mkdir -p service_data/sam3d-cache service_data/sam3d-logs
sudo install -d -o 10001 -g 10001 -m 750 /var/lib/rallymate/gpu-state
sudo chown -R 10001:10001 service_data
sudo chmod 750 service_data
```

API/Worker 以 UID `10001` 运行。数据、SAM cache/log 和 GPU 锁目录可写；模型、校准资产和 SAM runtime 主目录只读。同机 Worker 共用 GPU 状态目录，保留现有物理 GPU UUID 锁约束。

**选择与现有模型匹配的 Worker 镜像：**

- `deploy/Dockerfile` 是 RTMPose CUDA 镜像路线，需要检测和 RTMPose 权重，不包含 SAM3D 环境。
- SAM3D 的第三方运行时、模型资产和解释器要求参照 [SAM 环境说明](autodl/SAM_ENVIRONMENT.md)和[部署说明](autodl/SAM_DEPLOYMENT.md)，账户配置和服务启动使用本文的 Compose 流程。把已验证的 SAM Worker 镜像写入 `RALLYMATE_WORKER_IMAGE`，清空 `RALLYMATE_POSE_PRESET`，设置 `RALLYMATE_POSE_BACKEND=sam3d`、`RALLYMATE_POSE_PROFILE=analysis`、`RALLYMATE_POSE_NATIVE_KEYPOINT_FORMAT=mhr70`，填写容器内 checkpoint/config 路径。外部 Python 解释器也须存在于镜像内。
- Compose 不自动构建 Worker 镜像，避免覆盖指定的 SAM 环境。RTMPose 路线可显式执行 `docker build -f deploy/Dockerfile -t rallymate-worker-rtmpose:local .`。

API 使用 CPU 镜像，模型路径用于原有配置与就绪检查；推理模型由 Worker 加载。评分资产与授权检查沿用原项目规则。

Compose 的默认评分证据注册表与当前发布生命周期一致：

```dotenv
RALLYMATE_SCORING_FEASIBILITY_REGISTRY=/app/metric-feasibility-pose-wave-v3.json
```

## 3. 私有配置与 HTTPS

```bash
python3 deploy/prepare_env.py --public-host app.example.com --admin-email admin@example.com
```

工具生成 `deploy/.env`、`deploy/secrets/metrics_token` 和 `deploy/secrets/grafana_admin_password`，拒绝覆盖已有文件。数据库密码、JWT 密钥、运维密钥和初始管理员密码分别随机生成；终端仅显示文件路径。初始管理员密码在私有 `.env` 中查看。

Linux 下 `.env` 权限为 `0600`，secret 目录为 `0700`；两个 secret 文件为 `0444`，供容器内非 root 用户读取，宿主其他用户无法穿过父目录访问。这些私有文件已从 Git 和 Docker 构建上下文排除。

编辑 `deploy/.env`：

| 配置 | 设置方式 |
|---|---|
| `PUBLIC_HOST` | 实际 HTTPS 域名，不含协议、路径或端口 |
| `RALLYMATE_TLS_DIR` | 宿主绝对目录，包含 `fullchain.pem` 和 `privkey.pem` |
| `RALLYMATE_MODEL_LICENSE_ACK` | 按已有授权设为 `enterprise`、`agpl-compliant` 或 `alternative-backend`；生产不接受 `development` |
| `RALLYMATE_WORKER_IMAGE` 和模型变量 | 与实际有效的推理环境一致 |
| 数据、模型、校准和 runtime 目录 | 实际持久目录，可使用绝对路径 |
| `RALLYMATE_GPU_DEVICE_ID` | 本轮使用的 GPU 索引或 UUID |
| `RALLYMATE_BACKEND_SUBNET` / `RALLYMATE_BACKEND_IP_RANGE` / `RALLYMATE_NGINX_IP` | 默认私网、动态地址池和固定网关地址；自定义时三者一起调整，网关地址位于动态池之外 |
| `SENTRY_DSN` / `SENTRY_FRONTEND_DSN` / `VITE_SENTRY_DSN` | Python、Node、浏览器项目 DSN，空值关闭对应集成 |

证书按现有域名流程签发和续期。Nginx 只替换 `PUBLIC_HOST` 模板变量，强制 HTTPS 并限制 Host。数据库密码、JWT 密钥和运维密钥无需写入 Nginx。

Nginx 用实际连接来源覆写 `X-Forwarded-For`、`X-Real-IP` 和 `CF-Connecting-IP`，并设置 HTTPS 协议；API 的 `FORWARDED_ALLOW_IPS` 只信任 Nginx 的固定私网地址，Node 的 `VINEXT_TRUSTED_HOSTS` 限定为公开域名。当前边界按客户端直连 Nginx 设计：若在前面增加 CDN 或其他代理，记录的来源是该代理地址，应单独配置并验证其受信任网段，不能直接信任浏览器传来的转发头。服务器内的 advice 请求固定访问 `http://api:8000`，仍携带当前用户凭据。

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml config --quiet
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build api frontend
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d postgres
docker compose --env-file deploy/.env -f deploy/docker-compose.yml --profile tools run --rm migrate
```

Alembic 迁移可重复运行。生产 API/Worker 只验证 PostgreSQL 结构，不在请求或 Worker 启动时执行 DDL。使用 `config --quiet` 可避免把展开后的凭据打印到日志。

## 4. 历史 SQLite 迁移

新站点可跳到下一节。已有历史数据时，**先迁移，再首次启动会创建管理员的 API**。导入器要求目标账户表为空，避免把旧队列混入已投入使用的账户数据库。

1. 停止接收新任务，等运行任务结束，再停止 API 和 Worker。
2. 使用 SQLite backup API 或 `sqlite3 .backup` 生成一致性副本，同时备份完整数据目录。不能只复制活动中的 `jobs.sqlite3` 而遗漏 WAL。
3. 把视频、请求、报告、上传分片及内部复核 SQLite 文件复制到 Linux 持久目录。
4. 将备份库放入挂载目录，例如 `service_data/migration/legacy.sqlite3`，先预演，再导入。

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm --no-deps api \
  rallymate-import-database --source /app/service_data/migration/legacy.sqlite3 --dry-run \
  --path-map 'D:/Projects/网球/service_data=/app/service_data'

docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm --no-deps api \
  rallymate-import-database --source /app/service_data/migration/legacy.sqlite3 \
  --path-map 'D:/Projects/网球/service_data=/app/service_data'
```

按数据库中实际保存的前缀填写映射；多个根目录或 Junction 路径可重复 `--path-map`。导入保留任务 ID、状态、完整摘要和空归属。活动任务、目标冲突或核对失败会阻止提交；相同记录和映射可重复导入。

路径映射不复制文件，也不改写请求 JSON 和报告正文。应检查复制后的实际文件；已完成历史不重新推理，需要重跑时按当前模型配置重新提交。旧记录 `user_id=NULL`，普通用户不可见。运维可以明确分配给指定账户：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec api \
  rallymate-account-maintenance assign-record --job-id JOB_ID --user-id USER_UUID --actor-id ADMIN_UUID
```

ID 从数据库或管理接口取得。命令只接受无归属且已终止的历史任务，保留审计，不改写视频和报告。

## 5. 启动与业务验收

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d api frontend nginx worker adminer
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec nginx nginx -t
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

`/health/live` 验证 API 存活；`/health/ready` 要求独立运维 Bearer key，检查数据库、模型文件和原有部署策略。API 就绪不代表 GPU 模型已完成加载，同时查看 Worker 状态并完成一次实际视频推理。

逐项验收：

1. 匿名访问工作台跳转登录，直接请求上传、历史、结果和视频返回 401。
2. 新用户注册后可立即进入工作台并查看自己的训练记录。已停用、已拒绝或仍待审核的旧账户访问训练接口时返回 403。
3. A、B 分别上传，B 请求 A 的任务、分片、视频、报告和删除接口返回 404。管理员也没有普通业务接口的跨用户读取权限。
4. 实际视频完成上传、排队、推理和结果回看，原视频 Range 播放正常；原有评分与推理结果保持一致。
5. 管理员重置密码时弹窗只展示一次临时密码，旧会话立即失效；临时密码登录后先修改密码再进入工作台。
6. 封禁和删除账户撤销其会话；最后一个有效管理员受到保护。运行任务先取消，终止后才能删除；删除后个人列表与资源读取均不可见。
7. 同浏览器切换 A/B、退出和多标签页操作，历史、活动任务、续传、训练笔记、球场校准均按账户隔离。

Nginx 显式配置 `client_max_body_size 2G`，上传体和读写空闲超时为 `3600s`，关闭请求与响应缓冲。API 默认上限为 `2147483648` 字节，分片使用原协议。HTTP 超时是连续空闲时间；域名前的 CDN/WAF 如有限制，需要同时调整。

## 6. Adminer、备份与文件回收

Adminer 仅监听宿主回环地址。建立 SSH 隧道：

```bash
ssh -L 8080:127.0.0.1:8080 user@server
```

访问本机 `http://127.0.0.1:8080`，系统选 PostgreSQL，服务器填 `postgres`，数据库名、用户和密码取自私有 `.env`。账户状态管理和密码重置通过 `/admin` 执行，保证会话撤销与审计。

每日备份 PostgreSQL 和同一时点的视频/报告目录，定期在独立环境恢复。进入维护窗口后暂停公网入口，等队列排空，再停止 API 和 Worker；备份期间也暂停 Adminer 等其他写入工具。以下命令以默认 `service_data` 为例，使用自定义数据目录时将 `data_dir` 改成实际宿主绝对路径。

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml stop nginx
# 队列排空后执行：
docker compose --env-file deploy/.env -f deploy/docker-compose.yml stop api worker
(
set -eu
umask 077
data_dir="$PWD/service_data"
mkdir -p backups
chmod 700 backups
backup_dir="$(mktemp -d "backups/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/database.dump.part"
mv -- "$backup_dir/database.dump.part" "$backup_dir/database.dump"
sudo tar --numeric-owner -C "$data_dir" -cpf - . > "$backup_dir/service-data.tar.part"
mv -- "$backup_dir/service-data.tar.part" "$backup_dir/service-data.tar"
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec -T postgres \
  pg_restore --list < "$backup_dir/database.dump" > /dev/null
(cd "$backup_dir" && sha256sum database.dump service-data.tar > SHA256SUMS)
printf 'Backup directory: %s\n' "$backup_dir"
)
```

每次备份使用独立目录；命令失败会保留 `.part` 文件并停止该备份块，之前的备份不被覆盖。确认本次数据库、文件归档和校验文件齐全后，按第 5 节恢复服务。另将私有配置、校准资产、模型版本与镜像版本备份到受控位置。

逻辑删除不直接移除文件。明确需要回收已删除任务时，先备份，再执行：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec api \
  rallymate-account-maintenance purge-record --job-id JOB_ID --actor-id ADMIN_UUID --data-root /app/service_data
```

回收限定在指定数据根目录内的终止、逻辑删除任务，拒绝越界路径和链接；其他任务仍引用的文件会保留并报告，独占文件继续回收。记录、调用统计和审计保留。

## 7. 监控与错误追踪

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.monitoring.yml up -d
```

监控自动配置 API/Worker 指标、Linux CPU/RAM、按 GPU UUID 区分的利用率和显存、QPS、响应头与完整响应的 P95/P99、任务状态和执行尝试耗时。API `/metrics` 使用独立 token，Nginx 公网入口拒绝访问；Exporter 只在私网使用。

Grafana `3001`、Prometheus `9090`、Alertmanager `9093`、可选 Uptime Kuma `3002` 均绑定 `127.0.0.1`，通过 SSH 隧道访问。Kuma 用 `--profile uptime` 启用，首次登录配置 60 秒探测和通知通道。完整配置见 [监控说明](monitoring/README.md)。默认告警在本地展示，配置接收端后才发送通知。

Sentry 覆盖 Python API/Worker、浏览器 React 与 Node 服务。空 DSN 关闭对应集成，浏览器 `VITE_SENTRY_*` 变化后需重建前端。保留异常类型、原始栈位置、用户 UUID 与安全的任务 ID、执行次数和后端信息；原始异常消息、密码、临时密码、Cookie、Token、申请理由、视频、报告内容和局部变量不上传。性能采样率默认 `0.05`，独立于错误事件采集。

管理员密码重置由后端生成临时密码并撤销旧会话，**不依赖 SMTP**；监控邮件通知属于独立可选功能。

## 8. 升级与恢复

升级前停止新任务、排空 Worker，备份 PostgreSQL、文件、配置和当前镜像。按“构建 → 显式 migrate → API/网页 → Worker → 业务验收”启动，结构迁移失败时不启动新应用。

Worker 使用同一发布版本的镜像；RTMPose 按第 2 节显式构建，SAM 使用已验证的对应版本镜像。完成备份并停止旧 API/Worker 后执行：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build api frontend
docker compose --env-file deploy/.env -f deploy/docker-compose.yml --profile tools run --rm migrate
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --wait api frontend
```

确认 API 健康检查通过、前端完成启动后，重启 Nginx，再启动 Worker。Nginx 的 upstream 在加载配置时解析 `api` 和 `frontend` 地址；只重建后端容器不保证 Nginx 一并重建，旧地址会导致 502。以下命令使 Nginx 重新解析服务地址：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml restart nginx
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d worker
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

SQLite 导入保留源库，结构变更以新增为主。旧版没有账户权限边界，回滚前先停止公网入口，再在隔离环境恢复升级前的数据库和文件快照。新增账户后的数据通过 PostgreSQL 备份保留，不自动反向写回旧 SQLite。

恢复演练使用独立项目名 `rallymate-restore` 和私有 `deploy/.env.restore`，先恢复数据库与文件，再启动应用。该配置复制备份时的数据库、JWT 等秘密，并设置独立数据目录；下面示例使用项目根目录下的 `service_data_restore`。同时把后台网络设为未占用的子网，例如 `172.29.72.0/24`、动态池 `172.29.72.128/25` 和 Nginx 地址 `172.29.72.2`，避免与运行中的项目重叠。只启动恢复用 PostgreSQL 和 API 时没有宿主端口映射。

以下命令要求恢复项目的数据卷和数据目录为空。将 `backup_dir` 设为选定的完整备份目录，并确保 `.env.restore` 的 `RALLYMATE_DATA_DIR` 与 `restore_data` 相同。

```bash
(
set -eu
backup_dir="$PWD/backups/选定的备份目录"
restore_data="$PWD/service_data_restore"
(cd "$backup_dir" && sha256sum --check SHA256SUMS)
sudo install -d -o 10001 -g 10001 -m 750 "$restore_data"
sudo tar --numeric-owner -C "$restore_data" -xpf "$backup_dir/service-data.tar"
docker compose --project-name rallymate-restore --env-file deploy/.env.restore \
  -f deploy/docker-compose.yml up -d --wait postgres
docker compose --project-name rallymate-restore --env-file deploy/.env.restore \
  -f deploy/docker-compose.yml exec -T postgres \
  sh -c 'pg_restore --single-transaction --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$backup_dir/database.dump"
docker compose --project-name rallymate-restore --env-file deploy/.env.restore \
  -f deploy/docker-compose.yml --profile tools run --rm migrate
docker compose --project-name rallymate-restore --env-file deploy/.env.restore \
  -f deploy/docker-compose.yml up -d --wait api
)
```

完整备份已包含表结构与 Alembic 版本，因此先 `pg_restore`，随后运行当前版本的显式迁移。核对账户、任务数、归属及视频/报告文件后，再按维护窗口切换入口并完成上传与回放验收。恢复应用保持容器内 `/app/service_data` 路径，数据库中的文件引用无需因宿主恢复目录变化而改写。

常规停机使用带有相同 `--env-file`、`-f` 和项目名参数的 `docker compose stop`。`down -v` 会删除命名卷中的数据库和监控历史，不用于日常升级。
