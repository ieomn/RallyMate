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
- 相对参考总分、逐项测量、球与球拍观察，以及原视频的常驻回放入口。SAM 3D Body 结果默认显示官方人体表面回放，可切换原片叠加和侧视；原片/关键点回放仍可选择。旧版由关键点生成的白模接口保持下线，历史文件和离线维护工具保留。模型来源在技术详情中保留。
- 结果页集中展示动作回放、球场俯视模拟、训练指导五维图、训练建议和语言模型问答。五维为移动质量、准备与引拍、挥拍协调、触球控制、随挥与衔接；动作间隔在随挥与衔接中作为补充依据，落点统计独立保留在球场模块。
- 球路按整段视频的全部跟踪记录重建，按时间分段分析方向与画面内速度；播放时将轨迹叠加到对应视频帧，短缺口插值单独标识。
- 五类共 24 项最新动作定义：底线、发球、接发、网前、步伐。
- 底线/发球/接发的二维运动片段、规则类型与阶段估计；尚未确认触球，不把候选当击球次数。
- 导入已有任务报告或分析摘要，导出 Markdown、独立 HTML 及 JSON 备份。
- 按所选动作核验视频证据，明确解释未识别和证据不足；MiMo 上游默认关闭。

原有事件测量链覆盖分腿垫步、第一步启动、制动相关的 13 项指标；新增每个实际运动阶段的 13 类关节测量，包括双膝/双肘、支撑姿态、肩髋投影和头腕相对位置。FS/GS 两份 Word 的 298 项现在逐项返回原文、关联测量、计算定义、状态和具体缺口。目录完整不等于全部技术等级已可计算，未观测动作不补分。

### 五维训练报告与逐项解释

`process_evaluation` v1.2 保留六类底层过程证据以兼容历史报告，通过 `display_dimension_ids` 指定五个展示维度；`transition` 只作为随挥与衔接的补充，不混合分数。PDF 原图有六轴，这里的五维是依据 Word 阶段目的提出的展示归组：298 张原卡分别归入 70/78/72/25/53 张，每卡只归一组，目录数量不代表已评分覆盖。源文件指纹、原文定位和冲突保存在 `source_basis` 中。

页面始终提供“相对参考总分”：`process_evaluation.relative_reference` 使用 `relative-reference-v1.0.0` 公开试行规则。独立目标角差、腕速加速趋势和随挥减速趋势先按去重动作聚合；每规则为 `50 + (动作均分 − 50) × n/(n+3)`，维内规则等权、五维等权。缺少可计分方向的维度保留 50 中性起点（不是测得 50，也不是合格线），全无测量时仍明确给出 50 中性起点。SAM 的有效模型测量使用同一公式，不以置信度缺失拒绝参考分；测量覆盖和模型来源单独展示。该公式尚未经人群或教练标定，不表示原文 A–E 正式技术等级；`process_evaluation.overall_score` 仍保留正式总评空值。原始子项、规则、样本数、未评分原因和回放时间保持可查，Markdown、HTML、JSON 导出保存参考分及版本；导入时由有效测量重算，不信任传入缓存分数。

方向表示身体相对坐标下的画面运动方向；只有独立的目标方向证据才可用于移动方向评价，但这一缺口不抹去其他有效步伐测量。肩、髋、手腕的二维运动峰值先后不等于真实受力或三维动力链。触球控制需要姿态、球拍控制和经验证触球事件；落点命中率是独立结果统计，不能作为所有触球指标的统一门槛。衔接间隔也不直接等同衔接好坏。阶段识别与趋势测量使用相同的姿态数据，过程分仍可能受到阶段切分偏差影响，不能替代人工验证。

动作分析版本升级为 `stroke-motion-analysis-v2.2.1`，过程测量为 `process-motion-v1.1.0`。手腕局部漏检只影响依赖它的阶段；身份、时间轴和相机参照断点仍阻止跨段计算，速度突跳不生成虚假动作峰值。已完成任务再次读取报告时会从保留的逐帧姿态和主球员时间线重建过程测量，无需重新运行 GPU；原始任务摘要与视频不被覆盖。缺少这些产物的历史任务显示具体缺口。本轮不包含商城、社区、会员或在线裁判。

新增 `indicator_assessment`（`word-indicator-assessment-v1.0.0`）复用经文档哈希核对的 298 张原卡。正式事件测量、历史代理测量与同类动作的相关测量分别标识；三段运动候选不会冒充 Word 的十阶段。未知等级返回带原因的结构化状态。真实注视、足底受力、拍面角度和触球无法仅由普通二维骨架保证识别，原文也未给可直接执行的 A～E 数值切点。

本次对 RTMPose M/L/X 在 3 个现有视频、600 个同帧人物区域上完成 1,800 次推理。置信度≥0.5 的关键点比例分别为 88.65% / 89.84% / 89.60%，单区域中位耗时为 7.82 / 9.66 / 11.20 毫秒。L 在该覆盖指标略高，M 更快；缺少独立真值，不能据此宣布精度冠军或替换默认模型。详细变更、测试范围和尚未解决的条件见[本轮验收记录](docs/scoring-optimization-20261015.md)。

720p 球检测新增按需原始分辨率补检，原有高清与禁用逻辑保留。两段视频的 96/48 帧样本中，有球观测帧分别从 45/4 增至 49/7；第二段新增观测含静止场边球，仍需活动球身份与触球/落地验证，不能据此声称评分精度提升。五维原文依据、缺分定位、回放语义与实测结果见[训练五维修订记录](docs/training-dimensions-20261016.md)。

2026-10-16 回放更新：白模相关展示与五类服务接口已下线，产品启动不再创建其运行线程或自动生成任务；历史数据库和媒体保留。原视频改用手部半秒短尾迹与脚部位置标记，支持关闭，排除插值点和身份修复帧，并在身份、漏检、时间或分辨率断点处断开；画面留白时叠层与视频保持等比对齐。前端 291 项测试及类型、lint、生产构建通过，后端 45 项相关回归通过；当前报告的播放、定位、标记开关和旧接口 404 已现场验证。此变更仅调整显示，不增加评分证据。

[SAM 3D Body](https://github.com/facebookresearch/sam-3d-body) 的 DINOv3 与 ViT-H 已接入本地推理，可输出原生 70 关节、关联测量及真实人体网格。结果页默认提供官方淡蓝材质的人体表面原片叠加，并可切换 90° 侧视及原片/关键点；侧视相机居中仅影响展示。网格来自模型的 `pred_vertices` 和官方拓扑，不由关键点拼接；视频逐帧保留并复核来源 PTS。表面视频在评分完成后后台生成，页面自动显示完成结果。外观和未校准的单目几何不能代替评分准确性验证；权重访问按官方申请流程处理。

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

Windows 上配置好服务用 RTMPose 环境、SAM 的独立 Python 环境、完整模型和 `scoring-demo-web\.dev.vars` 后，在项目根目录执行下列命令。启动脚本默认使用真实 SAM DINOv3；可显式用 `-Model sam3d-vith` 或 `-Model rtmpose` 切换。它会从 `.dev.vars` 读取内部 API 密钥，启动一个 API/Worker 和网页，均只监听本机；已有服务占用端口时会停止启动，避免重复 GPU worker。SAM 权重缺失时会在启动前报错，不会自动替换模型。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -FrontendPort 8000 -ApiPort 8001 -Model sam3d-dinov3
# 前端源码变更后需重新构建，在上面的命令末尾增加 -Build。
# 读取迁移后的独立数据时，增加 -DataRoot "绝对路径"；不指定则使用 service_data。
# 另一个终端：将同一个网页入口用于朋友预览，保持该终端打开。
cloudflared tunnel --url http://127.0.0.1:8000
```

本地 DINOv3 启动默认给主服务 2 个、独立 SAM 进程 1 个 CPU 计算线程，分别用 `-CpuThreads`、`-SamCpuThreads` 覆盖；其他模型的主服务默认仍为 4 个线程。两者的 Torch inter-op 与 OpenCV 默认均为 1。CPU 线程数不限制 CUDA 显卡计算。主服务默认使用 `OMP_WAIT_POLICY=PASSIVE`、`KMP_BLOCKTIME=0`；SAM 默认 `-SamWaitMode passive`，独立覆盖子进程的等待策略，避免空闲线程忙等。SAM 子进程在导入计算库前设置本地线程池及硬上限，模型导入后记录实际值。诊断时可用 `-SamWaitMode runtime_default` 仅在子进程移除等待策略变量，恢复库默认行为。

先前的 CPU 等待策略优化，在本机 300 帧对照中使 API+SAM CPU 核秒减少约 92.4%，完整流程耗时增加约 5.4%。下面的新对照以这一低 CPU 配置为基线，不再使用原来的高占用配置放大收益。不同硬件应同时比较耗时、平均 CPU 占用和累计 CPU 核秒。官方网格视频默认优先使用显卡的 H.264 NVENC 编码，两路编码器预打开失败时一同回退到各 2 线程的 libx264，交付前仍逐帧解码核对全部 PTS。可用 `RALLYMATE_SAM3D_MESH_ENCODER=libx264` 显式选择软件编码。

2026-10-21 的 Windows DINOv3 本地默认配置增加了共享内存原帧传输、保留官方裁剪的 GPU 图像格式转换、主干和六个解码层的 CUDA Graph，以及两帧有界预取。GPU 转换与官方 CPU 输入逐位对齐，仍使用相同权重、浮点精度、分辨率及全部六次人体输出头；预取不跳帧。隔离 SAM 进程在 CUDA 上下文启动前启用阻塞等待，减少等待 GPU 时的 CPU 轮询，并读取实际驱动标志验证设置。该等待策略目前只验证了 Windows 上唯一可见的设备 0，活动上下文会拒绝修改；其他环境可显式设置 `RALLYMATE_SAM3D_CUDA_WAIT_MODE=runtime_default`。网格 NPZ 在内存计算文件校验值后一次写入，父进程仍核对实际磁盘内容；官方渲染代码保持一致。

在本机 RTX 5070 Ti / 20 逻辑核上，用同一视频的 18–28 秒、300 帧片段，优化前后分别预热后测量三次：完整推理流程中位数从 51.756 秒降至 50.912 秒（快 1.63%）；API+SAM CPU 核秒从 47.219 降至 36.578（降 22.53%），平均使用逻辑核数从 0.905 降至 0.714（降 21.09%）。CPU 统计覆盖 API 与 SAM 的完整前台采样区间，不包括后台网格渲染或短命编码进程。官方预览从提交到就绪的中位数为 73.410→73.607 秒，基本持平，没有稳定提速。三轮评分对象和判级完全相同；原生坐标/完整网格在原容差内，最大二维差异为 0.000184 像素，不能表述为全部浮点输出逐位相同。前视/侧视各 300 帧的 PTS 和完整网格绑定通过复核，样本视觉检查未见退化。此结果针对该片段，不代表所有视频或整段长视频的性能保证。

本地启动保留显式环境覆盖：`RALLYMATE_SAM3D_IPC_TRANSPORT=npy`、`RALLYMATE_SAM3D_GPU_PREPROCESS=0`、`RALLYMATE_SAM3D_FORWARD_GRAPH=off`、`RALLYMATE_SAM3D_PREFETCH_FRAMES=0`、`RALLYMATE_SAM3D_CUDA_WAIT_MODE=runtime_default` 可逐项恢复对照路径。上述 DINOv3 默认值不应用于 ViT-H 或 RTMPose；CUDA 缓存仍沿用官方每次清理策略。原始重复测量、数值和媒体核验记录保存在本机 `.codex_tmp/local-migration-20261019/speed-20261021/`。

后续一轮在相同低 CPU 基线上继续优化了完整解码器：固定索引留在显卡，六层、六个人体头、五次二维/三维更新及完整网格投影一起进入 CUDA Graph。原局部图绑定保留供回退，正常路径实际执行主干图与完整解码图。独立诊断中，每人 CUDA 同步调用从 177 次降至 61 次，小型 CPU→GPU 传输从 150 次降至 34 次；这些计数不能当作 GPU 利用率。最终输出的 21 个 CUDA 张量批量回传后统一等待，已有 CPU 面片数组保留；同一 GPU 输出树的新旧回传逐字节一致。父进程的磁盘校验/归档与下一帧推理重叠，最多保留两帧，仍按顺序在校验完成后发布，并在评分和官方预览前排空、等待线程退出。失败或取消不发布后续结果。

本轮以刚才的 **50.912 秒**配置为基线，沿用同一视频 18–28 秒、300 帧、逐帧处理及两人设置，比较各三次预热后的实测：推理中位数 **50.912→45.344 秒，耗时减少 10.94%**（新配置三次 43.917/45.422/45.344 秒）；API+SAM CPU 工作量 **36.578→32.406 核秒，减少 11.41%**。平均占用中位数 0.714394→0.709465 个逻辑核，三轮均值却从 0.699604 升至 0.716527，不能宣称平均 CPU 占用稳定继续下降。官方预览提交到就绪中位数 73.607→69.900 秒，首轮仍更慢，不能保证每次都提速。CPU 范围仍为前台 API+SAM，不代表整机或后台渲染总 CPU。

新配置保持官方权重、浮点精度、输入分辨率、所有六轮输出及完整网格；三轮相对评分对象、正式总分和判级完全相同。二维最大差异 0.000198364 像素、网格顶点最大差异约 0.000001371 模型单位，原容差未放宽。六层完整返回树另经 6 个动态输入、726 个数组叶子复核；独立克隆使三类投影视图变为连续布局，具体步长差异已记录。前视/侧视每轮各 300 帧的 PTS、原生网格绑定和官方渲染配置通过复核，30 对原尺寸视觉样本未见明显退化；编码后像素并非逐位相同。这些是该片段的复现结果，不是有真值的精度评测或所有视频的速度保证。

当前 DINOv3 默认新增 `RALLYMATE_SAM3D_COMPLETE_DECODER_GRAPH=1`、`RALLYMATE_SAM3D_BATCH_OUTPUT_TRANSFER=1`、`RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE=1`。三者分别设为 `0`，并传 `-CpuThreads 4`，即可恢复上一轮对照配置。显式覆盖继续保留；关闭完整解码图的前置绑定时不自动启用它，内部阶段诊断或缓存复用模式下也不自动启用批量回传。所有候选、失败观察和逐轮记录保存在 `.codex_tmp/local-migration-20261019/speed-next-20261021/`。两线程三次试验之间包含一次单线程探索及重启预热，非随机交错实验；单线程没有优于两线程，四线程候选平均 CPU 占用略升的结果也完整保留。

SAM body 模式默认启用经过来源与数值核查的提示点和旋转转换优化，不改变权重、精度、分辨率或帧步长；设环境变量 `RALLYMATE_SAM3D_VECTORIZE_PROMPTS=0`、`RALLYMATE_SAM3D_VECTORIZE_ROTATIONS=0` 可分别恢复原执行路径。完整模式保留原实现。

网页访问无需账号，内部 API 密钥仅供服务端使用。不要把 `.dev.vars` 提交到 Git。启动成功只说明进程已启动，仍需检查健康状态并实测上传、分析与回放。

更新代码后，暂停提交新任务；如果改动了前端，先在项目根目录执行 `npm.cmd --prefix scoring-demo-web run build`，构建成功后再重启。执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restart_preview_service.ps1 -FrontendPort 8000 -ApiPort 8001 -Model sam3d-dinov3` 可重新加载服务，该脚本不会自动构建。使用独立数据目录时须继续传入相同 `-DataRoot`，RTMPose 模式须继续传入 `-Model rtmpose`。脚本检查模型文件、端口进程和队列，模型缺失或任务忙碌时拒绝重启，并保留现有 Cloudflare 进程及地址。若只有网页未启动、API 仍在运行，可执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_preview_web.ps1 -FrontendPort 8000`，并保持该终端打开。两者均支持 `-FrontendPort` 与 `-ApiPort` 参数。

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
