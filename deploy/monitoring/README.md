# RallyMate 监控

这套叠加配置提供 API/Worker/数据库可用性、HTTP QPS 与 P95/P99、任务队列、Linux CPU/RAM、每卡 GPU 利用率和显存、执行尝试平均耗时。Grafana 自动加载 Prometheus 数据源和「RallyMate 运行监控」看板。

在 Linux NVIDIA 主机上运行。`node-exporter` 只读挂载宿主机 `/proc` 和 `/sys`，启用 CPU、内存、负载与内核信息采集。v1.12.1 的 [CPU 采集器](https://github.com/prometheus/node_exporter/blob/v1.12.1/collector/cpu_linux.go)还从 `/sys` 读取 CPU 在线状态、隔离状态和节流计数，因此保留这两个挂载即可，无需挂载宿主根目录或配置共享挂载传播。Docker Desktop 下这些指标反映其 Linux 虚拟机资源。

DCGM 需要 NVIDIA 驱动和 Container Toolkit。GPU 曲线按 UUID 区分设备；本配置使用 DCGM 4.9 的比例与字节字段，利用率换算为百分数。GPU 利用率表示设备忙碌程度，执行尝试耗时包括 CPU 和后处理，两者都不是纯 GPU 内核性能测量。

## 启动与访问

在项目根目录使用主部署文档生成的 `deploy/.env` 和密钥文件。API 与 Prometheus 共用 `deploy/secrets/metrics_token`，Grafana 使用独立的 `deploy/secrets/grafana_admin_password`。两个文件均为单行值，不写入 YAML、Git 或命令行参数。宿主密钥目录权限设为 `0700`，文件设为 `0444`，供容器内非 root 用户读取；宿主其他用户无法穿过目录访问文件。

```sh
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.monitoring.yml up -d
```

管理页面均绑定 `127.0.0.1`：Grafana `3001`、Prometheus `9090`、Alertmanager `9093`。通过 SSH 隧道访问，例如 `ssh -L 3001:127.0.0.1:3001 user@server` 后打开 `http://127.0.0.1:3001`。Grafana 首次使用 `GRAFANA_ADMIN_USER` 和密码文件的值登录。已有 Grafana 数据卷中的管理员密码不会因修改文件而自动重置。

Prometheus 的 **Status → Targets** 应显示 `rallymate-api`、`rallymate-worker`、`node`、`dcgm` 为 UP。API 抓取使用文件中的 Bearer token，Worker 的 `9101`、node-exporter 的 `9100`、DCGM 的 `9400` 均不映射到主机端口。Prometheus 保留 30 天、最多 10 GB 数据，以先达到的限制为准。

## 告警通知

默认 `alertmanager.yml` 使用本地空接收器，告警可在 Prometheus 和 Alertmanager 查看，不发送任何通知。阈值写在 `prometheus/alerts.yml`：API/Worker 失联 2 分钟、数据库失败 1 分钟、CPU 超过 90% 或 RAM 超过 85% 持续 10 分钟、单卡显存超过 90% 持续 5 分钟、5xx 超过 5% 持续 5 分钟、响应头 P95 超过 5 秒持续 5 分钟。5xx 告警还要求 5 分钟至少 20 个业务请求。

运营者启用通知时，将 `alertmanager/` 复制到权限为 `0700` 的私有目录，选用下面一个示例作为该目录的 `alertmanager.yml`，再设置 `ALERTMANAGER_CONFIG_DIR` 为其绝对路径。配置和秘密文件需允许 Alertmanager 容器用户读取，父目录限制宿主其他用户访问。

| 示例 | 填写内容 | 私有目录下的秘密文件 |
|---|---|---|
| `email.example.yml` | SMTP 地址、发件人、登录用户名和真实收件人；保持 TLS | `secrets/smtp_password` |
| `webhook.example.yml` | 接收标准 Alertmanager JSON 的自建服务 | `secrets/webhook_url`、`secrets/webhook_token` |
| `feishu.example.yml` | 飞书自定义机器人，安全关键词设为 `RallyMate` | `secrets/feishu_webhook_url` |

飞书示例使用 Alertmanager 的自定义 JSON 消息体转发文本。启用了签名校验的机器人应通过实现签名的自建转发服务接收 `webhook.example.yml` 的通知。URL 文件包含完整 webhook 地址，不能放入仓库。Alertmanager 不替换配置中的 shell 环境变量；环境变量只选择挂载目录和文件，收件人等字段需要在私有 YAML 中填写。

先校验所选配置，再重启 Alertmanager：

```sh
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.monitoring.yml run --rm --no-deps --entrypoint /bin/amtool alertmanager check-config /etc/alertmanager/alertmanager.yml
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/docker-compose.monitoring.yml up -d --force-recreate alertmanager
```

显式启用后，运营者可在 Alertmanager 添加一条标为 `ManualNotificationCheck` 的测试告警，确认发送及恢复通知到达自己配置的接收端。本仓库的自动测试只使用本地配置和合成指标，不联系示例收件人或真实 webhook。

## 可选健康探测

追加 `--profile uptime` 启动 Uptime Kuma。它只在 `127.0.0.1:3002` 提供管理页，数据写入独立持久卷。首次登录创建管理员后，为 `http://api:8000/health/live` 添加 HTTP 探测，间隔设置为 60 秒。就绪探测使用 `http://api:8000/health/ready`，在该监视器的请求头中设置 `Authorization: Bearer <RALLYMATE_API_KEY 的值>`；这里使用运维密钥，不使用普通用户 JWT 或指标密钥。未提供运维密钥时就绪端点返回 401。公网 HTTPS 入口也可单独添加探测。探测的通知通道需在 Kuma 中由运营者配置。

## 校验与版本

运行 `pytest tests/test_monitoring_config.py` 检查网络暴露、密钥引用、看板和告警契约。设置 `RALLYMATE_TEST_MONITORING_TOOLS=1` 后还会使用固定版本容器运行 `promtool`、`amtool` 和告警规则测试；这些命令不映射主机端口。

镜像固定版本及镜像清单摘要：Prometheus `3.13.5` LTS、Alertmanager `0.35.0`、Grafana `13.3.1`、node-exporter `1.12.1`、DCGM/Exporter `4.7.0-4.9.0`、Uptime Kuma `2.5.11`。版本与配置依据在 2026-11-17 核对：[Prometheus 发布列表](https://prometheus.io/download/)、[Prometheus 抓取配置](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)、[Alertmanager 通知配置](https://prometheus.io/docs/alerting/latest/configuration/)、[Grafana 文件预置](https://grafana.com/docs/grafana/latest/administration/provisioning/)、[node-exporter 容器使用](https://github.com/prometheus/node_exporter#docker)、[NVIDIA DCGM 安装与容器](https://docs.nvidia.com/datacenter/dcgm/latest/installation/install-dcgm-exporter.html)、[Uptime Kuma 发布记录](https://github.com/louislam/uptime-kuma/releases/tag/2.5.11)。
