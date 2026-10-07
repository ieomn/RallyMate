# RallyMate · 网球动作分析

上传视频，在本机或自己的 GPU 服务器进行动作识别，再按原文查看逐段测量、录入人工技术评审和回看依据。小米 MiMo 只辅助解释已经核验的结果，不能修改分数。

## 项目交接与源码

代码仓库：[ieomn/RallyMate](https://github.com/ieomn/RallyMate)，主分支 `main`。零基础接手者先读[完整交接手册](docs/RallyMate项目完整交接手册_零基础Codex接手版.md)，其中有架构、13 张流程图、接口、限流、备份恢复和可复制的 Codex 指令。

```bash
git clone https://github.com/ieomn/RallyMate.git
cd RallyMate
```

仓库包含前后端源码、契约、模型配置清单、测试、本机/AutoDL 部署与监控脚本及交接文档。模型权重、私人视频/标注、任务数据库、真实配置和密钥、依赖安装目录及构建产物另行提供或按文档重建；克隆完成不等于 GPU 环境和历史任务数据已经准备好。详见[AutoDL 环境重建](deploy/autodl/README.md)与[模型部署预设](models/rtmpose/deployment-presets.json)。

本仓库为公开仓库，文档里的真实任务编号和临时网站地址已脱敏。维护者通过受限监控获取当前地址，朋友通过自己的授权获取管理访问；GitHub 推送不会自动更新 AutoDL 服务。

## 当前功能

- 新上传会话采用 128 KiB 分块，兼容已有 4 MiB 会话；支持 SHA-256 校验、断点续传和幂等合并，排队与推理进度、刷新恢复任务。
- 动作参考分、逐项测量、球与球拍观察，以及默认不显示骨架的 H.264 回放。
- 球路按整段视频的全部跟踪记录重建，按时间分段分析方向与画面内速度；播放时将轨迹叠加到对应视频帧，短缺口插值单独标识。
- 五类共 24 项最新动作定义：底线、发球、接发、网前、步伐。
- 底线/发球/接发的二维运动片段、规则类型与阶段估计；尚未确认触球，不把候选当击球次数。
- 导入已有任务报告或分析摘要，导出 Markdown、独立 HTML 及 JSON 备份。
- 按所选动作核验视频证据，明确解释未识别和证据不足；MiMo 上游默认关闭。

当前稳定测量链覆盖分腿垫步、第一步启动、制动相关的 13 项指标。24 项目录不等于 24 项都已具备可靠评分；未观测动作不补分，参考分与证据就绪度分开显示。

### 分支与报告兼容

`main` 在 `024d240` 同步后端测量、评分 API、上传与服务端代理，并保留原有前端。当前完整界面和后续姿态准确度、可解释技术评分工作在 `codex/pose-scoring-accuracy`；`codex/scoring-report-rebuild` 的 `ed5481c` 保留为此前完整预览基线。

报告 API 默认返回 `legacy-v1` 兼容表示，当前开发网页显式请求 `report_contract=current` 获取完整新测量结构。两种表示共用真实测量值，不能把缺测转为技术扣分。原始 `summary.json` 默认按原字节提供，可显式请求 `report_contract=legacy-v1` 下载兼容副本。两份已发布注册表通过 Git 属性保留原始字节，权威 SHA-256 不变。

### 按七份 Word 进行技术评审

报告中的「技术评审」以具体视频时段为单位：选择原文指标或技术要点，回看片段，记录判断依据及下一步建议。298 张 FS/GS 指标卡提供人工 A～E 或无法评价；另外 246 条视觉要求记录观察到、未观察到或无法评价。没有充分观察条件不能判 E。原文明确可选的阶段不因未出现而扣分。

FS01-M02/M04/M05 与 FS09-M05 增加独立来源测量：髋高度下降、双膝角变化、速度连续性摘要、踝距与髋宽比、同人后继事件间隔。新增量保存在 `source-aligned-measurements.json`；旧特征名称、单位及参考分不被重新解释。阶段是待核验候选，二维数值不是实际触地、真实重心或已经标定的技术等级。

评审通过 `GET/POST /v1/jobs/{job_id}/technical-review` 保存到数据目录的 `technical-reviews.sqlite3`，导出接口为同路径下的 `/export`。修订保留历史，并绑定原视频、原文和事件/人物产物的内容指纹；多位评审人的意见分别保存。评审身份为填写者自报，不能视为认证或签名。导出的教练标签候选需独立裁定、事件真值校验及来源隔离的数据划分后，才能进入现有标定流程。

七份 Word 未定义技术百分制换算、扣分权重或数值等级切点，页面不据此杜撰分数。3 张原卡存在定义冲突，另 97 张卡缺少阶段起止定义，当前可记录无法评价和原因；不能以人工画一个时段代替对原文的修订。现有测量证据参考分单独保留作为辅助信息。

### 球路与实时回放

`GET /v1/jobs/{job_id}/trajectory` 从 `frames.jsonl` 生成 `ball.reconstruction`，包含轨迹片段、原始观测/插值标记、片段时间与分析，以及按视频帧去重的观测覆盖率。旧的 `ball.observed` 保留最长跟踪记录，供旧客户端兼容；新页面使用重建结果。短时插值不参与动作评分，长时间漏检保持断开；画面内速度的单位为归一化坐标/秒，不是实际球速。

任务运行时页面最多约每 15 秒读取一次已写入的轨迹；有处理帧计数时还要求计数变化。轨迹在本次上传的本地视频上同步显示，刷新后仍可继续读取；纯净回放在任务完成后可播放。已完成任务的旧 `frames.jsonl` 可直接生成新球路，无需重跑模型；旧导出报告需要重新读取任务后导出才能包含新字段。

## 本机运行

需要 Node.js 22.13+、已配置模型依赖的 Python 环境和本地模型文件。模型权重、私人视频、运行产物和凭据均不进入 Git。

```powershell
# 首次安装 Python 服务依赖；在已配置 PyTorch 的环境中运行
python -m pip install -e ".[service]"

# 终端一：API 与推理 worker
$env:PYTHONPATH="src"
# 默认使用注册表中的 RTMPose-M Halpe26；球、球拍和轨迹检测仍使用 YOLO
Remove-Item Env:RALLYMATE_POSE_PRESET -ErrorAction SilentlyContinue
python -c "from rallymate_service.cli import dev_main; dev_main()"

# 终端二：网页
cd scoring-demo-web
npm ci
npm run dev
```

默认网页为 `http://localhost:3000`，API 为 `http://127.0.0.1:8000`。网页通过同源代理上传，不需要把 API 密钥放入浏览器。`GET /health/ready` 检查分析服务是否就绪。Windows 的 service 依赖包含 ffmpeg wheel；Linux 镜像安装系统 ffmpeg，生成 H.264 回放。

Windows 上已经配置好 RTMPose 环境、模型和 `scoring-demo-web\.dev.vars` 后，在项目根目录执行下列命令。它会从 `.dev.vars` 读取内部 API 密钥，启动一个 API/Worker 和网页，均只监听本机；已有服务占用端口时会停止启动，避免重复 GPU worker。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -FrontendPort 8003 -ApiPort 8001
# 前端源码变更后需重新构建，在上面的命令末尾增加 -Build。
# 另一个终端：将同一个网页入口用于朋友预览，保持该终端打开。
cloudflared tunnel --url http://127.0.0.1:8003
```

网页访问无需账号，内部 API 密钥仅供服务端使用。不要把 `.dev.vars` 提交到 Git。启动成功只说明进程已启动，仍需检查健康状态并实测上传、分析与回放。

更新代码后，暂停提交新任务；如果改动了前端，先在项目根目录执行 `npm.cmd --prefix scoring-demo-web run build`，构建成功后再重启。执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restart_preview_service.ps1` 可重新加载默认 `8003/8001` 服务，该脚本不会自动构建。脚本检查端口进程和队列，忙碌时拒绝重启，并保留现有 Cloudflare 进程及地址。若只有网页未启动、API 仍在运行，可执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_preview_web.ps1`，并保持该终端打开。两者均支持 `-FrontendPort` 与 `-ApiPort` 参数。

以上相对路径命令必须在项目根目录运行；从其他目录操作时，将 `-File` 后面的路径换成脚本的绝对路径并加双引号。

运行期间可在另一个 PowerShell 窗口执行 `powershell -ExecutionPolicy Bypass -File .\scripts\full_service_status.ps1` 查看端口、进程和健康状态；执行 `powershell -ExecutionPolicy Bypass -File .\scripts\watch_full_service.ps1` 查看 API/Worker、网页和 Cloudflare 的实时日志。日志位于 `.codex_tmp\full-service\`，按 `Ctrl+C` 只会停止查看，不会停止服务。

已有 RTMPose 环境可使用 `scripts/run_rtmpose_service.ps1` 或 `scripts/run_local_inference.ps1` 选择对应模型预设。只有显式设置 `RALLYMATE_POSE_PRESET=yolo-baseline` 或传入同名参数时，才回滚到 YOLO Pose。

## MiMo 配置与防护

参见[前端说明](scoring-demo-web/README.md)与[MiMo 反代计划](deploy/autodl/MIMO_PROXY_PLAN.md)。本机服务端配置放在忽略提交的 `scoring-demo-web/.dev.vars`；当前 AutoDL 使用受保护的 `deploy/autodl/runtime.env`。不要把密钥写入 `NEXT_PUBLIC_*`、`VITE_*`、源码、测试样例或 Git 远程地址。

模型端点固定，输入字段、长度、来源、频率与并发受限。上游默认关闭；满足启用条件后，模型也只能选择服务端已核验事实与建议编号，展示文字由服务端生成，不能自由诊断或修改分数。无视频、所选动作证据不足、未知输出、超时或错误时提供确定性说明。出现身体不适的输入直接返回暂停练习，不调用模型。现有内存限流不是持久预算；任务授权与全局预算完成并验收前，不开启公网付费调用。

## 部署

[AutoDL 部署指南](deploy/autodl/README.md) 说明如何在 GPU 服务器上运行推理、网页与 Cloudflare 隧道。[通用部署指南](deploy/README.md) 提供 Docker、Nginx 同源网关、HTTPS 和 DNS 验证步骤。GPU 推理服务与网页分别运行；DNS 只负责域名解析。

网站访问无需登录，持有入口链接即可上传视频、启动推理及查看对应任务结果。内部分析 API 仍校验 Bearer key，由服务端网关注入，浏览器无需持有密钥。每次部署或切换隧道地址后，都应验证真实上传、推理进度与回放链路。

## 验证

```powershell
$env:PYTHONPATH="src"
python -m unittest tests.test_service tests.test_service_boundaries tests.test_training_evaluation tests.test_technique_assessment tests.test_user_demo tests.test_annotated_video_codec
cd scoring-demo-web
npm test
npm run typecheck
npm run lint
```

2026-09-15 本机验收：通过网页同源接口上传 97 秒、2,911 帧的网球视频，约 137 秒完成推理；返回 77/100 参考分、13 项测量、轨迹和 H.264 视频。浏览器核验视频 1280×720、97.03 秒，解码无错误。该记录只证明本机链路，并非动作准确率或云端性能承诺。
