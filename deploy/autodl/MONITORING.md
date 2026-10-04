# AutoDL 持续运行健康监测

> 公开源码版本：历史任务编号和临时访问网址已脱敏；完整记录由维护者私下保管，不能用说明性任务标签调用实际 API。

## 当前运行位置：本机（2026-10-01）

用户明确告知云服务器已关闭，本轮没有尝试连接或恢复云端。项目改为 Windows 本机运行：网页 `http://127.0.0.1:8000/`，内部 API `http://127.0.0.1:8001/`，均仅监听回环地址；使用本机 RTX 5070 Ti 与 RTMPose 环境。启动入口为 `scripts/start_local.ps1`，需要构建时加 `-Build`，不开启 Cloudflare，MiMo 真实调用关闭。内部 API 密钥仅在本机配置与进程之间共享，网站不要求账号密码。

已有 AutoDL Codex 心跳维持 `PAUSED`。本轮手工检查本机服务，不代表已建立本地持续守护、定时巡检或重启后自启动；云端关机期间不能产生新监控快照。后续恢复云端必须重新核对实例和 SSH 入口，不能套用下方历史健康结论。开发与验收交接见 [本地运行与击球脚步转体开发交接](../../docs/本地运行与击球脚步转体开发交接_2026-10-01.md)。

本机真实浏览器完成既有 56.7 MiB 视频的 15 块上传及推理：30.765 秒 / 923 帧全部成功，Worker 用时 85.54 秒，流水线 85.019 秒；GPU/RTMPose 元数据有效，H.264/yuv420p 回放 Range 请求为 206，轨迹为 `ready`，浏览器定位与播放正常。2 段发球运动候选仍不是确认击球，FS01/FS02/FS09 各 7 段构成前端的 21 段脚步事件。最终本机数据库有 32 个成功任务、1 个原有失败任务，无排队或运行任务。验收数据保留在已忽略的 `.codex_tmp/local-recovery-20261001/`，没有迁移关机云端上的独有历史任务。

## 最近一次恢复启动验收（2026-09-29）

SSH 入口已迁移至 `connect.cqa1.seetacloud.com:26691`；核对新入口与原先受信任主机公钥一致后登记 `known_hosts`，继续拒绝未知或变更的主机身份。本机 `check_remote.py` 与交接手册已同步端口，受限监控身份保持不变。管理员密码不写入项目或部署文件。

原项目、模型、虚拟环境、前端构建、配置和 15 个历史成功任务均保留，启动前没有排队或运行任务。原 Supervisor 已停止，使用既有 `manage.sh start` 恢复 API、Worker、Web、cloudflared、monitor。51 个核心生产源码与本机逐字节哈希一致，没有重新发布整套源码或更换模型。当前 GPU 为 NVIDIA RTX 6000D，PyTorch `2.8.0+cu128` 的 CUDA 运行正常。

- 新 Cloudflare 随机入口的免登录首页返回 200；就绪接口内容为 `ready`，无原因项，RTMPose/Halpe26 预设与注册表有效。当前域名只从健康快照或 `manage.sh url` 获取，不在公开文档固化。
- 公网分块验收使用 59,442,657 字节（约 56.7 MiB）、30.765 秒、923 帧的既有发球视频，共 15 个分块；重新打开会话时跳过已完成的 2 个分块，重复 complete 返回同一任务。上传流程用时 436.06 秒，平均 0.13 MiB/s，包含会话、续传确认与合并提交开销。本次本机经 Cloudflare 的实测上传仍偏慢，不能据此声称上传性能已解决，也不能推广为所有用户的固定速度。
- 验证任务成功，排队 0.14 秒，Worker 77.82 秒，流水线 77.466 秒；923 帧全部处理，记录真实 GPU/RTMPose 运行。保留 2 个发球动作候选，确认击球次数仍为未知，不以候选替代确认触球。
- 输出为 1920×1080、H.264/yuv420p、30.765 秒视频；公网 Range 请求返回 206，抽帧核对无骨架或调试覆盖。球轨迹接口为 `ready`，观测和插值继续区分。
- 使用当前任务从公网取得的结果，调用与部署版本一致的前端报告模块，成功生成 Markdown 和独立 HTML。报告按钮本来就在浏览器内生成可读报告；公网网关不开放原始 `analysis-report.html` 产物路径，该路径返回 404 属于既有白名单约束，不能作为报告导出失败的判断依据。本轮未重新自动点击浏览器下载按钮。
- API/Web 继续只监听本机 8001/8000；未带内部密钥访问 API 就绪接口返回 401。Web 进程的有效 `MIMO_ADVICE_ENABLED=0`，无网站账号密码配置，没有调用付费模型。
- 五进程持续运行，最终健康快照新鲜且无告警；成功任务共 16 个，无排队、运行或失败任务。新端口监控密钥验证通过：无关命令仍只返回快照，端口转发被拒绝。18 项本地监控测试通过。

云端每 60 秒采样已恢复。本机原有 15 分钟 Codex 心跳当前为 `PAUSED`，本轮保留该设置，没有声称已自动恢复本机定时通知；手动只读检查通过。本次恢复、域名变化及验证成功事件已由主任务处理，后续心跳不重复通知。此次没有验证或新增 AutoDL 控制台开机自启动设置。

私有验收数据保存在本机已忽略的 `.codex_tmp/autodl/recovery-verification-20260929/`；云端运维目录保存此次恢复验收摘要和核心文件哈希清单。公开文档不包含真实任务编号、临时网址或访问凭据。

## 最近一次已验证发布：建议解释与 MiMo 默认关闭（2026-09-24 13:05 UTC）

用户授权后已部署 `20260924T130527Z`，18 个指定文件核对哈希一致，包含建议解释、配置防护、测试和文档。服务器暂存构建、类型检查和 38 项针对测试通过；激活前后检查无运行/排队推理任务。仅重启 Web，API、Worker、Cloudflare 和监控进程保持运行；域名未变。

`runtime.env` 已明确设置 `MIMO_ADVICE_ENABLED=0`，没有更换模型密钥。公网首页免登录，新文案上线；3 次接口验收全部 200：无视频为 no_video，发球为 motion_only 且 providerStatus=disabled，接发为 insufficient_evidence。真实浏览器再核对接发解释及补证建议正常。未请求真实 MiMo、未新建推理任务。持久预算、任务授权等仍是待实现计划，不可自动开启付费调用。

回滚材料位于服务器 `service_data/ops/releases/pre-advice-20260924T130527Z.tar.gz`、同目录的配置备份及 `scoring-demo-web/dist-before-20260924T130527Z`。配置备份仅服务器拥有者可读，禁止输出内容。下方轨迹/挥拍版本继续有效。本次发布与验证已通知，心跳不要重复报告。

## 前期 MiMo 建议接口本地验证（2026-09-24 13:01 UTC）

本轮新增 `MIMO_PROXY_PLAN.md`，以及所选动作证据隔离、未识别解释、前端上下文隔离、默认关闭真实调用、固定官方按量上游的本地代码。前端构建、103 项测试、typecheck、lint 通过；本机浏览器验证无视频说明与切换类别后的清理。没有调用真实 MiMo、没有改写凭据、没有发布本轮文件。

当时只完成方案和本地防护，随后按上方记录部署。任务授权、持久配额与预算仍待实现，不可将默认关闭误报为已完成防刷，也不可仅加入新密钥就自动开启公网付费服务。本机原配置为 Token Plan 类型，不能直接用于本网站后端。后续启用条件与分阶段验收见 `MIMO_PROXY_PLAN.md`。

13:01 UTC 只读检查健康、快照新鲜且无告警；新完成任务 `历史完成任务E（编号脱敏）` 已通知。上述本地变更和验证已由主任务处理，心跳不重复通知，不盲目发布。

## 前一次轨迹与挥拍发布（2026-09-24 12:34 UTC）

发布 `20260924T123453Z` 已完成，17 个指定生产文件逐一核对哈希一致；仅切换 API、Worker、Web，Cloudflare 与监控进程未重启。不要重复发布整个未提交工作区。

- 轨迹重建 v1.1.0：完整已播放片段、短尾迹、最近片段概览；双端运动支持下补齐最多 600 ms 的短缺口，虚线与观测分开，长缺口不强连。骨架关闭，识别点默认隐藏。
- 挥拍分析 v1.0.1：底线/发球/接发的运动片段、估计阶段、二维指标和回放定位；已删除长候选列表。规则分类不作为确认触球。旧任务由 API 从原始姿态产物重新派生，版本缓存已更新。
- 高清输入新增 1536 小球补检，基础人物/球拍与低分辨率路径保持原配置。拒绝过大的新增框并只作跨尺度去重；仍可能识别场边静置球，镜头移动尚无背景补偿，不能将观测覆盖当准确率。
- 本地 128 项相关后端测试通过。前端全量 76 项及后续 24 项针对测试、类型检查、lint、构建通过，真实公网底线/发球面板、片段定位与轨迹已验证。
- 云端验证任务 `历史动作验收任务C（编号脱敏）` 成功：30.765 秒 / 923 帧视频，排队 1.25 秒，Worker 95.95 秒，流水线 95.27 秒；2 段发球运动分析。球观测帧 439/923（47.56%，旧样本为 280/923），不代表识别准确率。高清补检让此次耗时比旧版约 76 秒增加约 20 秒。
- 接发代码已接入，但本次单人发球视频缺少对手发球至来球的连续上下文，正确返回证据不足；没有伪造接发次数或确认击球次数。

上述本地变化、验证完成事件已由主任务处理；心跳只需按新快照处理后续变化。真实公网地址已从公开文档移除；维护者通过受限快照或 `manage.sh url` 获取当前入口。

`monitor.py` 作为第五个独立 Supervisor 进程运行，默认每 60 秒采样。15 分钟的外部心跳读取快照；服务器采样不依赖本机在线。它不重启其他进程，不修改任务状态或推理参数。

每次采样检查：

- API、Worker、Web、cloudflared、monitor 的 Supervisor 状态。
- 本机 API 存活和就绪检查、网页 HTTP 状态，以及当前 Cloudflare 随机域名的公网 HTTPS 请求。公网检查不携带内部 API key，不关闭 TLS 证书校验。
- SQLite 各状态任务数量、运行任务的实际进度变化和租约；持久化阶段、已处理帧数、总帧数、百分比及重试次数的指纹，默认超过 900 秒无变化或租约已过期时，标记 `job_progress_stalled`，由人工复核。仅刷新数据库时间戳或续租不会重置计时；任务持续推进但运行很久本身不会被当作卡住。`last_progress_seconds` 表示进度指纹未变的时长，`last_update_seconds` 单独表示数据库更新时间距今多久。
- 新出现的失败与成功完成任务编号和时间，磁盘剩余空间、GPU 利用率/显存/温度、分块上传数量和暂存字节数。

`jobs` 查询通过 SQLite `mode=ro` 连接。输出不包含视频文件名、私有路径、任务原始错误、HTTP 响应正文、GPU 进程列表或任何凭据。磁盘低于 2 GiB 或 5% 时记录低空间状态。公共域名仅为本次隧道地址，不是固定配置。

## 启用与查看

上传这次新增文件和修改后的 Supervisor 配置后，在 AutoDL 上执行：

```bash
cd /root/autodl-tmp/rallymate
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf reread
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf update monitor
deploy/autodl/manage.sh status
deploy/autodl/manage.sh health
```

上述更新只加入 monitor，不需要为了启用采样而重启正在推理的 API/Worker/Web/Tunnel。本文提供启用步骤，文件编辑本身不代表监测已经在云端启动。新部署执行 `manage.sh start` 会同时启动五个进程。

完整样本以原子替换写入 `service_data/ops/health.json`。历史快照写入同目录的 `health.jsonl`，单文件上限 5 MiB，保留五份轮转备份；`monitor.log` 保存进程自身的标准输出，由 Supervisor 独立轮转。状态游标写入 `monitor-state.json`。这些文件继承仅拥有者可读写的权限。

`manage.sh health` 等价于 `.venv/bin/python deploy/autodl/monitor.py --snapshot`，只读取已有 JSON，不读取 `runtime.env`，不访问网络、不运行命令，也不执行清理。快照超过三倍采样周期（默认 180 秒）标为 `stale` 并退出 3；快照不存在也退出 3。新鲜快照退出 0，调用方仍须检查 `overall_status` 和 `alerts`，不能把退出 0 当作服务全部健康。

`--once` 会执行一次真实采样并写文件，包含上传暂存清理，用于服务器上手动诊断；它不是 SSH 只读入口。

## 事件与清理语义

首次采样分别为数据库已有失败和成功任务建立独立游标，避免历史任务刷屏。此后的 `job_failed`、`job_succeeded`（含任务编号和完成时间）、告警出现/解除和域名变化保存在 `recent_events`，保留最近 24 小时、最多 200 条，供 15 分钟心跳按时间和事件内容去重。`new_events` 仅表示本次 60 秒采样新增事件。运行中任务的进度指纹与计时同样持久化，重启 monitor 不会因重复心跳清零卡住时长。外部心跳应只在有意义的变化、完成、失败或需要用户处理时通知；状态不变时保持安静。

上传清理委托项目 `UploadStore.cleanup()`：使用每个会话的锁，清理超过 24 小时的未完成分块，或已经提交任务后残留的分块和临时拼接文件。保留 `manifest.json` 墓碑，不删除原始视频、推理结果、队列记录。清理子进程最长运行 15 秒，锁等待或异常会记录 `upload_cleanup_deferred`，留待下一次采样；监测进程不会绕过锁删除文件。`eligible_sessions_checked` 表示符合清理条件的会话检查数，不代表本轮实际删除的文件数。

可在仅服务器可读的 `runtime.env` 设置 `RALLYMATE_MONITOR_INTERVAL_SECONDS`（默认 60，允许 15–3600）和 `RALLYMATE_MONITOR_STUCK_SECONDS`（默认 900，最小 60）。调整后只重启 monitor；不要因为长视频耗时较长就自动取消任务。

## SSH forced-command 只读快照方案

给监控心跳使用一对独立的 SSH 密钥，私钥仅保存在心跳执行端。公钥添加到 AutoDL 的 `authorized_keys` 时，使用以下单行前缀；公钥正文和安装由部署操作者完成：

```text
restrict,command="/usr/bin/env -i /root/autodl-tmp/rallymate/.venv/bin/python -I /root/autodl-tmp/rallymate/deploy/autodl/monitor.py --snapshot" ssh-ed25519 <监控公钥正文> rallymate-monitor-readonly
```

`restrict` 禁止端口转发、代理转发、X11 转发、PTY 和用户 rc；forced command 固定调用 `--snapshot`，忽略客户端要求执行的原始命令。`env -i` 不把登录环境或 SSH 原始命令传给程序，Python `-I` 禁用用户 site-packages 和 Python 环境变量的影响。不要给这把密钥添加其他不受限制的同公钥条目，也不要把它用作日常远程管理密钥。可按稳定的监测端地址再加 `from="..."` 限制。

安装后从监测端使用指定身份文件、`IdentitiesOnly=yes`、`BatchMode=yes` 和 `StrictHostKeyChecking=yes` 连接已登记主机密钥的 AutoDL SSH 端口。预期无论客户端给出什么命令，都只返回快照 JSON；交互 shell、SFTP 和端口转发均不可用。至少验证一次正常读取、指定无关命令仍只返回 JSON、以及端口转发被拒绝，再让自动化使用该密钥。

此限制仅约束这把公钥的 SSH 会话。主机上的脚本和快照仍需由管理员维护，监控端不应获得可写目录或管理员凭据。

## 本机 15 分钟心跳入口

安装受限公钥后，将对应私钥保存在本项目已被 Git 忽略的 `.codex_tmp/autodl/monitor_key`。原维护者的监控环境为 Python 3.14 / Paramiko 5.0.0；新电脑应在专门用于监控的解释器中安装 `paramiko==5.0.0`，例如 `python -m pip install paramiko==5.0.0`。这不是 GPU/RTMPose 环境的安装命令，也不会配置 SSH 权限。完成凭据与主机指纹交接后，原电脑的 Codex 心跳可运行（新电脑替换解释器与项目路径）：

```powershell
& 'C:\Python314\python.exe' 'C:\Users\Admin\Documents\网球\deploy\autodl\check_remote.py'
```

`check_remote.py` 只连接 `connect.cqa1.seetacloud.com:26691` 的 root 受限公钥入口，固定发送 `snapshot`，由远端 forced command 返回 JSON。它调用 `load_system_host_keys()` 和 `RejectPolicy`，要求该地址和非默认端口的主机密钥已登记到系统用户的 `known_hosts`；不会自动接受未知或变更的主机密钥。它禁用 SSH agent 和其他私钥搜索，不回退密码，不请求交互终端、SFTP 或端口转发。读取上限 2 MiB，连接/认证与快照读取都有超时。

输出是单个精简 JSON：`notify` 指示是否有需要处理的新变化，`notify_reasons` 给出原因，`health` 为最新健康摘要，`new_events` 为去重后的有意义事件，`last_public_url` 保留最近一次已知公网入口。第一次健康检查只建立基线，保持安静；新告警、连接中断、恢复、任务失败、域名变化会标记通知。同一离线状态不会在每次心跳重复报警。异常只返回固定代码，不回显可能包含凭据或路径的异常原文。

去重数据原子写入 `.codex_tmp/autodl/heartbeat-state.json`。连接失败只更新连接故障信息，保留之前的 `last_good_snapshot`、公网地址和事件游标，不用空结果覆盖上次成功记录。新鲜但不健康的快照仍会反映其 `attention` 状态。脚本以 JSON 表达离线情况并正常退出，心跳应读取 `notify` 与 `notify_reasons`，不能仅依据进程退出码判断健康。

脚本还用 `git ls-files --cached --others --exclude-standard` 将文件范围限制在 `src/`、`scoring-demo-web/`、`deploy/autodl/`、`tests/`，排除环境文件、密钥、凭据名称、缓存、二进制后缀、符号链接和超过 2 MiB 的文件。它只对原始字节计算 SHA-256，不解析或展示源码；输出新增/修改/删除数量与受限目录内的相对路径，单类最多 200 条。首次建立代码基线后，新变化标记 `local_code_changes_for_review`，交给 Codex 审查；该脚本不会安装软件、上传文件、部署或重启服务。扫描失败保留之前的代码摘要。
