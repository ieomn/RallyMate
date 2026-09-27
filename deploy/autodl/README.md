# AutoDL 单机 GPU 与 Cloudflare 临时域名

> 公开源码版本：历史任务编号和临时访问网址已脱敏；完整记录由维护者私下保管，不能用说明性任务标签调用实际 API。

部署目录为 `/root/autodl-tmp/rallymate`。AutoDL 上独立运行 API、GPU Worker、完整网页和 Cloudflare Tunnel，浏览器通过同一个 HTTPS 域名访问网页及视频推理接口。本机无需保持运行。

```text
浏览器 → Cloudflare HTTPS → AutoDL cloudflared → 127.0.0.1:8000 网页
                                                  ↓ 内部 Bearer key
                                            127.0.0.1:8001 API
                                                  ↓ 本地任务队列
                                               GPU Worker
```

网页直接打开，无登录账号和密码。API key 只在 AutoDL 网页服务与 API 之间使用，保存在未提交的 `runtime.env` 中；不要把它放进前端构建变量或公开日志。拥有临时网址的人可以使用网页并提交推理任务。

## 运行前准备

部署工具假定下列文件已经安装或上传，不会自动安装软件：

- `.venv/bin/python`、`.venv/bin/supervisord`、`.venv/bin/supervisorctl`：Python 环境及项目依赖。
- `tools/node/bin/node`：满足前端 `package.json` 的 Node 版本。
- `tools/cloudflared`：Linux 可执行程序。
- `scoring-demo-web/node_modules/` 和通过 `npm run build` 生成的前端产物。
- 项目所需的 `models/`、`calibration/`、注册表及其引用文件。

`runtime.env.example` 默认私人研发环境 `development`、GPU `0`，未声明任何生产或商业模型授权。

## 本次部署环境与重建步骤

本次 AutoDL 环境为 Ubuntu 22.04、Python 3.12.3；镜像自带 PyTorch `2.8.0+cu128` 和 torchvision `0.23.0+cu128`。服务采用 `--system-site-packages` 虚拟环境复用这套 CUDA 运行时。已安装并验证的主要 Python 版本列在 [`requirements-constraints.txt`](requirements-constraints.txt)，其中 NumPy 为 `1.26.4`、OpenCV 为 `4.11.0.86`。这份文件约束关键兼容性版本，并非包含全部间接依赖的完整锁文件。

系统 `ffmpeg` 为 `4.4.2`，Node 为 `22.23.2`，cloudflared 为 `2026.9.1`。以下命令用于新实例重建，修改已有环境前先停止服务；新镜像重建后仍需完成 GPU 和视频任务检查。

### Python 与推理依赖

先进入已上传的完整项目，确认 `python` 指向 AutoDL 镜像的 Python 3.12.3。不要另外安装一套 CPU 版 PyTorch：

```bash
cd /root/autodl-tmp/rallymate
python --version
python -c 'import torch, torchvision; print(torch.__version__, torchvision.__version__, torch.version.cuda, torch.cuda.is_available())'
python -m venv --system-site-packages .venv
```

先安装 NumPy、Cython、旧版 setuptools 和 wheel，再安装依赖。`setuptools==69.5.1` 满足 `<81`，保留旧 OpenMMLab 依赖所需的兼容接口；编译 `xtcocotools` 时关闭构建隔离，使扩展使用运行时相同的 NumPy 1.x，避免 NumPy ABI 不匹配：

```bash
.venv/bin/python -m pip install \
  -c deploy/autodl/requirements-constraints.txt \
  numpy Cython 'setuptools<81' wheel
.venv/bin/python -m pip install --no-build-isolation \
  -c deploy/autodl/requirements-constraints.txt \
  -e '.[service,rtmpose]' scipy xtcocotools
.venv/bin/python -m pip install --ignore-installed --no-deps \
  -c deploy/autodl/requirements-constraints.txt supervisor
```

`xtcocotools` 如果从源码编译，需要 C/C++ 编译器及当前 Python 的头文件。Supervisor 的 `--ignore-installed --no-deps` 不能省略：即使镜像已安装同版本，也需要在 `.venv/bin/` 生成本项目的 `supervisord` 和 `supervisorctl` 入口。检查：

```bash
.venv/bin/python -c 'import numpy, torch; import xtcocotools._mask; print("NumPy:", numpy.__version__); print("PyTorch:", torch.__version__, "CUDA:", torch.cuda.is_available())'
.venv/bin/supervisord --version
ffmpeg -version
```

确认 NumPy 为 `1.26.4`、CUDA 为 `True`，且 `xtcocotools._mask` 能导入。使用 `mmcv-lite` 是当前 RTMPose 后端的既定方案，不需要额外安装完整 MMCV CUDA 扩展。

### 模型与项目数据

保留完整源码、根目录评分注册表及其引用文件、`calibration/` 和 `models/rtmpose/` 中的 JSON 清单。当前 `rtmpose-m-halpe26-online` 预设所需的最小权重为：

- `models/yolo26n.pt`
- `models/rtmpose/rtmpose-m_halpe26_256x192.pth`

同时保留 `models/rtmpose/deployment-presets.json`。MMPose 的模型配置从已安装包的 `.mim/configs/` 查找，完整项目包含相应的跨平台路径解析。上传已有权重时沿用项目登记的文件，并携带配套清单。

### Node、前端构建和 Cloudflare

以下为本次使用的官方 Linux x64 下载地址及已经核对的 SHA-256。下载文件放入 `tools/downloads/`，可执行程序的位置与运维脚本一致：

```bash
mkdir -p tools/downloads tools/node
curl --fail --location --retry 3 \
  --output tools/downloads/node-v22.23.2-linux-x64.tar.xz \
  https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz
printf '%s  %s\n' \
  d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307 \
  tools/downloads/node-v22.23.2-linux-x64.tar.xz | sha256sum --check
tar -xJf tools/downloads/node-v22.23.2-linux-x64.tar.xz \
  -C tools/node --strip-components=1

curl --fail --location --retry 3 \
  --output tools/downloads/cloudflared_2026.9.1_amd64.deb \
  https://pkg.cloudflare.com/cloudflared/pool/main/c/cloudflared/cloudflared_2026.9.1_amd64.deb
printf '%s  %s\n' \
  3be76adc4185d36a0bfb4c2dd8663292f0ed363797f2180333b513b43c81d419 \
  tools/downloads/cloudflared_2026.9.1_amd64.deb | sha256sum --check
dpkg-deb -x tools/downloads/cloudflared_2026.9.1_amd64.deb \
  tools/downloads/cloudflared-package
install -m 755 tools/downloads/cloudflared-package/usr/bin/cloudflared tools/cloudflared

export PATH="/root/autodl-tmp/rallymate/tools/node/bin:$PATH"
node --version
tools/cloudflared --version
cd scoring-demo-web
npm ci && npm run build
cd ..
```

每次 SHA-256 检查均应显示 `OK` 后再解包。前端构建使用仓库中的 `package-lock.json`，不要上传 Windows 的 `node_modules` 代替 Linux 安装。`manage.sh start` 只启动已构建的前端，不会替你运行构建。

### 创建服务配置

在 AutoDL 终端中执行：

```bash
cd /root/autodl-tmp/rallymate
cp -n deploy/autodl/runtime.env.example deploy/autodl/runtime.env
chmod 600 deploy/autodl/runtime.env
chmod +x deploy/autodl/manage.sh deploy/autodl/run-process.sh
```

用随机值替换 `runtime.env` 中的 API key。配置使用 shell 赋值格式，包含空格或特殊字符时必须正确引用；仅将可信配置写入该文件。API 与 Worker 读取同一个环境文件，确保模型预设和数据目录一致。启动前应核对当前项目使用的模型预设，不应为了运行而静默更换模型。

## 启停与日志

```bash
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh start
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh status
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh url
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh logs worker
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh logs cloudflared
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh restart
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh stop
```

`logs` 默认同时跟踪五个服务（含 60 秒健康监测），`Ctrl+C` 仅退出日志查看。Supervisor 在后台运行，SSH 断开不会停止服务；所有 socket、PID 和轮转日志位于 `service_data/ops/`，不依赖系统 Supervisor 或 systemd。`restart` 会重启五个进程，因此会中断当前推理；更新代码或 `runtime.env` 后，在任务空闲时执行。API 和网页均只监听回环地址。健康快照、告警与 SSH 只读访问配置见 [`MONITORING.md`](MONITORING.md)。

如需开机后自动恢复，可在 AutoDL 平台的自定义开机命令中填写：

```bash
/root/autodl-tmp/rallymate/deploy/autodl/manage.sh start
```

这只是可配置命令，脚本不会代为设置平台开机任务。实例关闭时服务也会停止。

## 临时域名行为

Tunnel 使用 `--protocol http2`，由 AutoDL 主机主动连接 Cloudflare，无需开放 AutoDL 的入站网页端口。`manage.sh url` 从当前 Tunnel 的日志中提取 `https://…trycloudflare.com`。连接建立需要少量时间，刚启动尚未出现地址时可稍后再查。

Cloudflare Quick Tunnel 重启后会分配新的随机域名。整个服务重启、AutoDL 实例重启，或 cloudflared 崩溃后被 Supervisor 拉起，都可能改变网址；请重新执行 `manage.sh url`，更新书签或分享链接。短暂网络重连是否更换域名以实际输出为准。需要长期固定域名时再改用 Cloudflare named tunnel 与自己的域名。

HTTP/2 仍需要 AutoDL 到 Cloudflare 的出站网络可达；仅进程状态为 RUNNING 不代表公网链路成功。部署完成应从公网网址验证首页、任务提交、结果轮询和视频下载。Cloudflare 的请求体大小限制独立于项目上传限制，大视频上传应以公网烟测为准。

## 检查服务

```bash
curl --fail http://127.0.0.1:8001/health/live
curl --fail http://127.0.0.1:8000/
```

`/health/ready` 和任务接口使用内部 API key；通过网页代理验证时无需用户输入 key。`status` 中所有程序 RUNNING 仅说明进程存活，GPU 推理是否可用必须由真实视频任务验证。原始视频、任务记录和结果写入 `service_data/`，更新代码时保留该目录。

## 首次部署验收记录

本次在 AutoDL RTX 4090 D 上使用默认 `rtmpose-m-halpe26-online`，通过公网匿名上传一段 11.72 秒、293 帧、1920×1080、7,105,847 字节的视频，任务 `历史验收任务A（编号脱敏）` 完成。后端记录的 `processing.elapsed_seconds` 为 30.975 秒；结果与轨迹接口通过检查，生成 7,690,104 字节的 H.264 / yuv420p 回放，Range 请求返回 HTTP 206 和所请求的 1024 字节。另一次从 AutoDL 经公网域名回到服务的任务也完成相同链路验证。这是单个输入在当时环境下的验收记录，不代表其他视频的处理时长保证。

## 本地代理与 DNS 排查

先在 AutoDL 检查 `manage.sh status`、API 和网页的回环地址，再核对 `manage.sh url` 给出的当前随机域名。进程未就绪、回环请求失败或 Cloudflare 日志未注册连接时，应先排查服务及 AutoDL 出站网络。

如果服务正常但本机访问出现 TLS 错误，可从可信 DNS 独立核实当前域名的 Cloudflare 边缘 IP，使用 `curl --resolve 域名:443:边缘IP https://域名/` 做一次诊断。只有普通访问失败、相同域名通过 `--resolve` 成功时，才进一步检查本机 Clash/TUN 的 DNS、Fake-IP 缓存或分流规则。本次本机出现过这种情况，解析返回 Fake-IP，而指定核实后的 Cloudflare 边缘 IP 能返回 HTTP 200；与 AutoDL 上的服务和 Tunnel 注册状态应分开判断。

`--resolve` 保留域名对应的 TLS 证书校验，不需要也不应通过关闭证书验证来掩盖问题。边缘 IP 和随机域名都不应写进应用固定配置；诊断结束后恢复正常 DNS 访问。

## 2026-09-24 上传与结果修复

网页现使用 [可续传分块上传](UPLOADS.md)，4 MiB 每块、两路并发、SHA-256 校验和幂等合并。真实 `IMG_5712.mov`（59,442,657 字节）公网验收分成 15 块，中断恢复时跳过已收到的 3 块，重复完成返回同一任务 `历史分块验收任务B（编号脱敏）`。本次上传及合并约 45.37 秒、1.25 MiB/s；完整 923 帧 GPU pipeline 处理 84.213 秒。此为当次线路与输入实测，不保证其他网络速度。

新任务默认输出无骨架、检测点或状态栏的清晰 H.264 回放；网页按最新反馈默认叠加 0.75 秒球路短尾迹，识别点和缺口插值保持关闭。已有六个成功任务的回放与预览也由原视频重新生成，帧数、帧率、时长一致，保留 `annotated.legacy.mp4` / `preview.legacy.jpg` 备份。一次性修复脚本 `clean_legacy_replays.py` 默认只读检查，显式 `--apply` 才替换产物。

新增主球员姿态与球拍时序候选检测器，区分动作候选和确认触球。该真实视频两段发球候选为 5.066–6.266 秒、20.832–22.098 秒，已逐帧复核。底线挥拍候选不强制分类为正手或反手，候选次数不参与击球计数。旧完成任务可由保存的帧记录重建候选，无需重新占用 GPU。

后续反馈核查确认：投产事件评分链路仍仅有 FS01/FS02/FS09 三类步伐事件，尚未实现底线正反手/切削专项分类或确认触球。长片 `历史长视频任务D（编号脱敏）` 的 57 段挥拍候选不能视为 57 次击球；页面移除展开的候选列表。技术评估 v1.3 在类别层明确区分“检测到运动但未分类”和“动作分析不可用”，不再用未观测概括能力缺失。阶段证据必须具有一致的动作身份，不能由另一类事件的同名阶段填充。

结果完成后可导出 Markdown 或无外部脚本的独立 HTML，JSON 保留为高级数据备份。候选区间、评价、缺失证据与限制写进报告；未完成任务禁止导出正式结果。

云端已启用第五个 `monitor` 进程，每 60 秒采样。Codex 当前任务已设置每 15 分钟心跳，执行只读 `check_remote.py` 并审查本地代码变化；无变化保持静默。只读监控 SSH 密钥验证了 forced command 与禁止端口转发，部署密钥不供日常巡检使用。详见 [持续监控与故障通知](MONITORING.md)。云端采样要求 AutoDL 实例运行，Codex 心跳执行要求本机与 Codex 可用。
