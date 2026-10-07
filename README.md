# RallyMate · 网球动作分析

上传视频，在本机或自己的 GPU 服务器进行动作识别，再查看练习参考分、分项观察和标注回放。小米 MiMo 只辅助解释已经核验的结果，不能修改分数。

## 项目交接与源码

代码仓库：[ieomn/RallyMate](https://github.com/ieomn/RallyMate)，主分支 `main`。零基础接手者先读[完整交接手册](docs/RallyMate项目完整交接手册_零基础Codex接手版.md)，其中有架构、13 张流程图、接口、限流、备份恢复和可复制的 Codex 指令。

```bash
git clone https://github.com/ieomn/RallyMate.git
cd RallyMate
```

仓库包含前后端源码、契约、模型配置清单、测试、本机/AutoDL 部署与监控脚本及交接文档。模型权重、私人视频/标注、任务数据库、真实配置和密钥、依赖安装目录及构建产物另行提供或按文档重建；克隆完成不等于 GPU 环境和历史任务数据已经准备好。详见[AutoDL 环境重建](deploy/autodl/README.md)与[模型部署预设](models/rtmpose/deployment-presets.json)。

本仓库为公开仓库，文档里的真实任务编号和临时网站地址已脱敏。维护者通过受限监控获取当前地址，朋友通过自己的授权获取管理访问；GitHub 推送不会自动更新 AutoDL 服务。

## 当前功能

- 新上传会话使用 128 KiB 分块，兼容已有 4 MiB 会话；支持 SHA-256 校验、断点续传和幂等合并，排队与推理进度、刷新恢复任务。
- 动作参考分、逐项测量、球与球拍观察，以及默认不显示骨架的 H.264 回放。
- 球路按整段视频的全部跟踪记录重建，按时间分段分析方向与画面内速度；播放时将轨迹叠加到对应视频帧，短缺口插值单独标识。
- 五类共 24 项最新动作定义：底线、发球、接发、网前、步伐。
- 底线/发球/接发的二维运动片段、规则类型与阶段估计；尚未确认触球，不把候选当击球次数。
- 导入已有任务报告或分析摘要，导出 Markdown、独立 HTML 及 JSON 备份。
- 按所选动作核验视频证据，明确解释未识别和证据不足；MiMo 上游默认关闭。

当前稳定测量链覆盖分腿垫步、第一步启动、制动相关的 13 项指标。24 项目录不等于 24 项都已具备可靠评分；未观测动作不补分，参考分与证据就绪度分开显示。

### 后端迁移与开发分支

`main` 同步了 `codex/scoring-report-rebuild` 在 `ed5481c` 的推理、时间窗测量、来源核验、技术评审 API、上传存储和服务端代理逻辑。原有网页客户端、页面、样式与宣传页保持不变；`scoring-demo-web/worker/gateway.ts` 属于服务端代理，是该目录中迁移的运行代码。新版前端与后续姿态准确度、可解释技术评分工作在 `codex/pose-scoring-accuracy` 继续。

后端增加的能力包括：逐段独立脚步测量、二维运动与转体窗口、处理帧进度、姿态预览，以及 FS01-M02/M04/M05 和 FS09-M05 的独立原文测量。原有测量含义和已保存产物不会被新测量覆盖。分数解释说明的是测量证据参考分的构成，并不把证据完整度当作技术动作正确率。

七份 Word 的来源审计位于 [评分规范索引](docs/scoring-reference-20261004/README.md)。`GET/POST /v1/jobs/{job_id}/technical-review` 与 `/export` 提供带来源绑定和修订历史的人工评审；这是服务端能力，主分支旧页面尚未接入新版评审界面。教练试标材料在 [操作指南](docs/教练视频标注操作指南_2026-10-04.md) 和 `examples/coach-annotation-pilot/`。没有充分证据或人工标定时保持无法评价，不自动生成 A～E 技术等级或百分制技术分。

报告 API 默认提供旧客户端兼容表示；需要完整新测量结构的客户端应在任务、报告或技术分析 GET 请求上显式添加 `report_contract=current`。已保存的原始分析产物仍保留自身版本，兼容表示不会改写原始文件。两份已发布注册表保留原始文件字节，以便全新 Git 检出也能通过既有 SHA-256 校验。

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

已准备好本机环境、模型和 `.dev.vars` 时，从项目根目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -FrontendPort 8003 -ApiPort 8001 -Build`。该入口先读取内部 API 密钥与本机路由配置，再启动 API/Worker 和网页，两个端口均监听本机；已有服务占用端口时拒绝重复启动。另开终端运行 `cloudflared tunnel --url http://127.0.0.1:8003`，并保持其运行。网站无需账号或密码；不要把 `.dev.vars` 中的 API 密钥提交到 Git。

更新服务前暂停提交新任务；服务端网页代理有改动时先执行 `npm.cmd --prefix scoring-demo-web run build`，成功后再运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restart_preview_service.ps1`。重启脚本会核验进程与空闲状态，沿用现有 Cloudflare 进程及地址，且不会自动构建。相对路径命令需在项目根目录执行；也可以给 `-File` 传加双引号的完整脚本路径。

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
