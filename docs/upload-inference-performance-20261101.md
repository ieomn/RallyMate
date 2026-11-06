# 上传与视频推理优化记录（2026-11-01）

## 2026-11-02 主干目标与实现进展

后续工作固定沿两条主线推进：先优化上传数据路径，再推进有界的 GPU 批预处理和批推理。参考 [CVAT/tusd](https://github.com/cvat-ai/cvat/blob/develop/cvat/apps/engine/tus.py) 的增量上传状态与后台任务交接，以及 [NVIDIA DeepStream Libraries](https://github.com/NVIDIA-AI-IOT/deepstream_libraries) 的缓冲复用和 CUDA 流依赖；不是直接替换现有模型或引入另一套服务。

上传代码已实现以下改动，生产服务是否加载这些代码须以实际重启记录为准：

- 新会话使用每会话 `state.sqlite3`，每个分块只更新对应行及累计计数。compact PUT（含 API 的前置长度校验）不读取完整分块清单；GET 恢复状态及完成校验仍读取清单。
- 现有 `manifest.json` 会话保持原分块大小与协议，不在传输中迁移。哈希、重复 PUT、并发锁、过期与配额检查保留。
- 文件 flush/fsync 和发布完成后才登记分块；Linux 额外同步目录项。数据库提交失败时不确认分块，重试修复未登记文件。
- 完成阶段仍逐块校验并合并一次；之后将已验证文件原子移交到任务输入位置，去掉第二次整份视频复制。不是完全消除合并，也不是内核零拷贝。
- Windows 使用不覆盖目标的 rename，Linux 使用 `renameat2(RENAME_NOREPLACE)`；入队异常、丢失响应和重试保留可恢复状态，不覆盖已有不一致文件。

实现位置：`src/rallymate_service/upload_state.py`、`uploads.py`、`api.py`。本地基准为 `scripts/benchmark_upload_storage.py`，测试 128 KiB、1/2/4 MiB，强制任务空闲，并将 PUT、合并/移交分开计时。测试目录独立；结果不包括网络、HTTP 网关、Cloudflare 或 GPU 推理，不能换算成公网传输提速。

500 MiB 使用 128 KiB / 1 MiB / 2 MiB / 4 MiB 分块时，PUT 数分别为 4000 / 500 / 250 / 125。代码默认仍为 128 KiB，保留慢链路兼容；`RALLYMATE_UPLOAD_CHUNK_BYTES` 可选择 1–4 MiB，已建立会话不受后续配置变化影响。公网超时和重传需要实际链路验证。

**本地存储实测：**同一 64 MiB 确定性字节流，每种配置三轮、交替新旧后端。下表为 PUT 调用耗时中位数，包含服务端哈希、文件同步、元数据提交及测量插桩，排除 payload 生成、客户端哈希、空闲检查、HTTP 和网络：

| 执行环境 | manifest / 128 KiB | SQLite / 128 KiB | SQLite / 1 MiB | SQLite / 4 MiB |
| --- | ---: | ---: | ---: | ---: |
| Windows / D 盘 | 5.490 s | 3.892 s | 0.565 s | 0.179 s |
| WSL Ubuntu / ext4 | 3.098 s | 2.727 s | 0.398 s | 0.138 s |

Windows 同分块大小的元数据改造减少约 29.1% PUT 耗时，Linux 约 12.0%；分块增大带来的请求/文件数量减少更明显。Linux 1 MiB 的新旧 PUT 中位数几乎相同（约 0.398/0.397 秒），并非每种配置都存在明显 SQLite 提速。128 KiB 的旧存储在 512 次 PUT 中全读 manifest 1024 次、全写 512 次，JSON 读写长度合计约 40.7 MB；新存储无全量 chunks 查询，每块一次查找和插入。JSON 长度不是物理磁盘 I/O 统计。

完整原始报告为 `reports/upload-gpu-mainline-20261102/upload-windows-64m-3runs.json` 与 `upload-linux-ext4-64m-3runs.json`。两后端 completion 均使用同一简化 rename 回调，未模拟旧 API 第二次视频复制，也未测 API 视频探测/请求持久化/实际队列提交；不能用该 completion 计时声称完整 API 加速。减少整份复制另外由 API 文件身份及恢复测试证明。

### Linux 执行环境

已确认本机 WSL2 Ubuntu 24.04.2、Linux 6.6.87.2 可访问 RTX 5070 Ti；安装了独立的 PyTorch 2.11.0+cu128 / Python 3.11 SAM 验证环境。官方 SAM 包可导入，61 项批处理、预处理及生命周期 CPU 测试在 Linux 通过。上传存储、API、文件移交测试也已在 Linux 执行，包括 Windows 环境不能执行的 symlink 测试。

复现安装入口为 `scripts/setup_linux_validation.sh`，需要已有 `uv` 和固定版本模型资产。默认虚拟环境位于 `~/.venvs/rallymate-service`、`~/.venvs/rallymate-sam3d`；Linux runtime 位于 `~/.local/share/rallymate/runtime`，模型资产复用现有路径，缓存与 NPY IPC 位于 Linux 文件系统。没有迁移或替换线上 Windows 服务。

当前 Linux SAM 验证使用 `RALLYMATE_SAM3D_IPC_TRANSPORT=npy`、`RALLYMATE_SAM3D_CUDA_WAIT_MODE=runtime_default`。现有 shared-memory IPC 与 blocking-sync 驱动策略仅验证 Windows，不能照搬启动环境。Linux GPU 可见、CPU 测试通过不等于完整 Linux 视频分析已经验收。

随后已在 WSL 上实际加载官方 SAM DINOv3，完成两个独立 worker 的同源 16 帧测试：70 点、18,439 顶点、36,874 面均输出并比较，batch8 稳态约 22.65 FPS。Windows/Linux 的等待策略、IPC 与文件系统不同，不能把性能差异只归于操作系统。当前验证覆盖 Linux SAM 推理，整套 Linux API/YOLO/视频渲染服务尚未迁移。证据：`linux-environment.json`、`linux-staging-comparison.json`。

WSL 直接读取 Windows 正在使用的 WAL 队列数据库曾出现 I/O 错误。测试现通过 `--idle-jobs-python /mnt/d/Projects/网球/runtime/rtmpose/.venv/Scripts/python.exe` 启用持久 stdio 桥接：数据库仍由 Windows 原生只读连接检查，每次读取最新队列状态，不使用快照绕过新任务。模型 mesh 缓存与归档目录也须在同一文件系统；Linux 基准产物放在 ext4，完成后复制 JSON 报告到项目目录。

本轮证据目录：`reports/upload-gpu-mainline-20261102/`。下文保留上一轮的历史测量和部署状态；其中“每块仍全量读写 manifest”等描述对应旧实现，新会话已由上述增量实现替代。

### 实时预览与推理之间的 CPU 竞争

2026-11-02 18:41–18:43 的只读检查发现，任务 `198ae44d-bd6b-4b91-97d4-f8980a64ebc6` 的 `frames.jsonl` 从约 888 MB 增至约 918 MB。实时 `/trajectory?sample_limit=128` 按文件大小/mtime 缓存，文件继续追加就使缓存失效；原 `build_trajectory_preview` 每次从头逐行 `json.loads`。`sample_limit` 只限制返回点数，不限制扫描量。普通运行中状态 GET 会提前返回，并没有同样的全量轨迹扫描。

部署入口 `run_dev_service.py` 将 API 和分析 worker 放在同一进程的不同线程；采样约 57.7 秒时，该进程累计 CPU 时间增加约 58.4 秒。一个约 92.9 KB 的真实帧记录中，轨迹所需字段约 2.0 KB，JSON 解码仍会处理完整姿态对象。重复全扫与 GIL 竞争路径已确认，但这些观察不足以把本次全部 FPS 下降都归因于轨迹接口。

已实现 `src/rallymate_service/live_trajectory.py`：活动任务缓存紧凑观察并按文件偏移增量读取，只读取新增完整行，复用相同轨迹算法。首访已有大文件仍需完整扫描；文件替换、截断、同长重写及任务重跑失效，不完整 UTF-8/JSONL 行留到下一次读取。最多 4 个任务、单任务 50,000 条观察、全局 100,000 条观察；这是对象计数上限，不是 Python 堆字节上限。超预算明确回退完整读取，完成态保留原完整读取路径。绘制仍遍历紧凑历史观察，没有声称整个预览为 O(1)。

真实源前 512 行在隔离临时副本中先载入 64 行，再进行 8 次追加刷新：后续全读累计解析 2528 行/221,956,637 字节，增量读取 448 行/38,395,769 字节，减少约 82.7%。预览调用中位数从 0.3470 秒降到 0.06684 秒（约 5.19 倍），9 个阶段完整预览逐字段一致，没有预算回退。仅度量轨迹预览，不代表整段视频提速 5 倍。报告：`reports/trajectory-incremental-20261102/benchmark.json`。

### 本轮部署与可选批推理配置

2026-11-02 19:27–19:29（北京时间），确认队列为空且两分钟内无上传会话写入后，通过既有受控重启入口重新加载服务。上传 SQLite 增量状态、受控文件移交与实时轨迹增量读取已在新进程中生效；新上传会话使用 **1 MiB**，旧会话保留原大小。API ready、前端 HTTP 200，原 Cloudflare PID 47252 保持不变，历史任务状态未改变。证据为 `reports/upload-gpu-mainline-20261102/deployment-verification.json`。

`scripts/restart_performance_preview.ps1` 保存了可复现入口：默认上传 1 MiB、SAM 单帧；显式 `-SamBatchFrames 8` 可选本机批处理配置（persons8/graph8/新 staging 关闭），也接受 4。仅在任务空闲时重启，批配置先检查 16 GiB 级显卡。本轮没有将所有公网输入默认改成批次：4K 会触发 64 MiB 待处理源帧预算拆批，实际人物数与 graph8 不一致时可能变慢；缓存预算也不是总显存上限。下一步推广重点是分辨率、人数变化、短尾及显存压力下的选择策略。

```powershell
# 已部署的上传/实时预览优化，保持 SAM 公共默认单帧：
.\scripts\restart_performance_preview.ps1
# 本机显式选择已经测量的多帧配置：
.\scripts\restart_performance_preview.ps1 -SamBatchFrames 8
```

本轮最后联合回归：Windows 121 项（120 通过，1 项 symlink 权限跳过），Linux 上传移交/轨迹 60 项全部通过；另有 Linux 上传存储、SAM 批处理、生命周期和桥接专项测试。测试与基准均未降低分辨率、删除关键点/网格或修改评分公式。朋友实际 500 MiB 文件的公网传输与完整分析仍需在真实链路计时，不能把本地存储、SAM 或预览的单项加速相乘当作总收益。

后续进展：同日已实现并实测 SAM BODY 真正多帧批推理，详见 [SAM 批推理实现与验证](sam3d-batching-20261101.md)。本文保留第一轮上传、检测及内存优化的验证范围；下文关于 SAM 尚未完成的描述对应当时状态。

本次依据当前源码、本机既有任务产物和 RTX 5070 Ti 小样本检测实验。没有取得朋友那次约 500 MB 上传的浏览器网络记录，因此不能把其全部耗时归因于 Cloudflare 或 GPU。之前的说明位于 `reports/system-resource-audit-20261026/report.html`，其中“为什么上传小包，却会反复发很长的清单？”对应本次已修复的回执问题。

## 已落地的修改

1. **单块确认。** 新网页在 PUT 中协商 `X-Upload-Acknowledgement: chunk-v1`；服务器只返回本块编号、大小、SHA256 等常量长度信息。旧网页继续兼容完整清单；断线恢复仍查询完整清单并核对哈希。前端进度使用增量计数，避免每次遍历全部已收块。网关使用有上限的增长缓冲，避免每个 128 KiB 请求固定分配 4 MiB。
2. **有界多帧检测。** `Yolo26Perception.detect_batch()` 真正将多个图像交给一次 YOLO 调用；相同尺寸分组保持矩形缩放，球补检按原有逐帧策略决定后再分组。流水线保存每帧自己的 PTS、源帧号及像素绑定，跟踪、相机状态、主体选择、SAM 姿态和评分仍按源顺序执行。
3. **完整姿态记录写盘后释放。** 推理不再积累整段 `sam_records`；网格归档成功后将完整原始记录保存到 JSONL。诊断阶段逐行读取，只保留实际使用的字段和 12 个核心关节；保存完整诊断报告后，内存只保留最终 summary 需要的状态和计数。原始 70 点和网格产物仍完整保留。诊断增加独立阶段计时。

上传数据、逐块哈希、断点续传、重复提交幂等性均保留。分块只是字节传输，不是视频裁剪。

## 实际证据及边界

**上传回执：**500 MiB、128 KiB/块，共 4,000 块。顺序成功上传的实际 JSON 序列化总量：旧回执 625,138,145 字节，新回执 1,010,890 字节，约少 99.84%。这是应用层未压缩回执量，含并发/压缩/重试的真实网络量不同；不是“上传速度提高 600 倍”。500 MB 十进制对应 3,815 块，旧回执 568,394,020 字节、新回执 964,084 字节。

**最近任务：**`b0fef192-28f7-4c31-bf51-b101f29997f2` 的旧 summary 记录 1,441 帧、213.999 秒，其中姿态 123.991 秒、检测 26.267 秒、评分 26.809 秒、产物校验 10.783 秒。解码消费端等待仅 0.045 秒，后台解码与其他阶段重叠。更长的 `0fe1b095-9a55-47a3-b100-a3961f07bc4c` 有 11,516 帧，总耗时 2,681.604 秒，姿态 1,704.332 秒、评分 374.332 秒。两者均为 SAM3D DINOv3；不能以压缩文件 MB 数直接预测推理时间，帧数、尺寸、人物数及后处理更相关。

**检测实测：**本机 RTX 5070 Ti，PyTorch 2.11.0+cu128，YOLO26n，imgsz=960，原球补检策略。每个视频取开头 32 帧，batch=1/2/4/8 各三次交替测量，预热单列，运行前及每批检查无正在执行/排队任务。

- 854×480 视频：检测 FPS 中位数依次为 **91.27 / 136.89 / 174.81 / 209.23**。
- 1280×720 视频：检测 FPS 中位数依次为 **56.45 / 84.59 / 110.04 / 122.80**。
- batch=8 的检测阶段约快 2.2 倍。以上排除上传、姿态、跟踪和评分，不是整段分析加速比。
- 720p 的 batch=8，检测基准进程 CUDA 已分配峰值约 560 MiB；8 张 BGR 原图约 21.09 MiB。此显存不包含正在运行服务的 SAM 模型。
- 样本内检测数量与类别顺序一致，但数值并非逐位相同。720p 最大框偏差约 0.465 像素、置信度偏差 0.0352；阈值附近可能改变后续结果，因此生产默认保留 batch=1。

完整证据：`reports/upload-inference-performance-20261101/detector-batches-32-frames.json` 和 `detector-batches-720p-32-frames.json`。复测脚本为 `scripts/benchmark_detection_batches.py`，须提供视频、模型、输出位置，建议带 `--idle-jobs-db`；这仍然仅是检测基准。

**诊断记录：**真实本地档案前 64 行，原记录与紧凑记录生成的整个诊断报告完全一致；JSON 体积由 1,093,254 字节降为 139,985 字节（约少 87.2%）。这是记录载荷体积，不能等同整个进程峰值内存下降幅度。

## 配置与后续验收

`RALLYMATE_UPLOAD_CHUNK_BYTES=1048576` 可将新上传会话设为 1 MiB，500 MiB 只需 500 次 PUT。范围 128 KiB–4 MiB。默认仍为 128 KiB：之前缩小分块是为了慢链路重试和进度反馈；应在朋友实际网络对比后选定。已部分上传的会话保留原分块大小。

`RALLYMATE_DETECT_BATCH_SIZE=4` 可启用 4 帧目标检测，允许 1–16。默认 1；批次源图预算 64 MiB，超大单帧单独处理。解码预取、主体初始化缓冲各有独立上限，不能把 64 MiB 当成进程总内存上限。多帧功能没有对 CUDA OOM 静默重试，避免掩盖真实资源配置问题。

开关通过启动进程环境传入 API/Worker，修改后须重启对应服务。PowerShell 示例（先确保服务空闲）：

```powershell
$env:RALLYMATE_UPLOAD_CHUNK_BYTES = '1048576'
$env:RALLYMATE_DETECT_BATCH_SIZE = '4'
& .\scripts\restart_preview_service.ps1 -FrontendPort 8000 -ApiPort 8001 -Model sam3d-dinov3
```

这是显式实验配置，不表示本次已启用。恢复 batch=1 后重启即可回到单帧检测。完整验收应固定同一视频、帧范围、模型和人数，交替对照上传耗时、整体耗时、GPU/CPU/内存峰值、跟踪身份、事件与评分，而不只比较 GPU 利用率。

仍存在的成本：每块上传 manifest 全量读写；精确诊断统计和输出数组仍是 O(帧数)；报告与评分仍有 CPU 工作。本次未将全片所有对象改成恒定内存，也没有进行朋友 500 MB 文件的公网端到端复测。

## 为什么不能直接把 SAM 改成 n 帧并行

当前 SAM 是逐人物请求、跨进程 IPC，部分 CUDA Graph 固定单人物输入形状。既有的“两帧预取”和“批量输出传输”不等于多帧模型前向。多个线程并发调用同一有状态模型或共享缓冲可能破坏帧绑定，并增加显存压力。

SAM 真批处理需要同步改造：多帧/多人 crop 组批、逐样本相机与人物元数据、IPC 缓冲槽、支持 batch 形状的 graph capture，以及有序拆分网格/关键点输出。可先从 n=2、4 验证，再考虑 8；不保证 n 倍加速。

CUDA 优化应以阶段 profile 为依据：复用模型和固定缓冲、减少 CPU/GPU 往返与不必要同步，再考虑混合精度和 TensorRT。`pin_memory()` 放在主线程临时执行不一定更快；真正复制/计算重叠需要 pinned buffer、独立 CUDA stream、事件依赖和正确的缓冲生命周期。[PyTorch 官方说明](https://docs.pytorch.org/tutorials/intermediate/pinmem_nonblock.html)

## 可用开源项目

- [TorchCodec](https://github.com/meta-pytorch/torchcodec)：视频直接解码为 PyTorch 张量，现有 Windows CUDA wheel；适合后续连同 GPU 裁剪/预处理一起改造。当前已有解码预取，先确认解码确为瓶颈。
- [PyNvVideoCodec](https://developer.nvidia.com/pynvvideocodec)：MIT，NVIDIA 官方支持，源码通过 NGC 分发，支持 Windows/Linux 的 NVDEC/NVENC 和张量互操作。GPU 解码后若马上转回 CPU NumPy，收益会被搬运削弱。
- [MMDeploy](https://github.com/open-mmlab/mmdeploy)＋TensorRT：官方[支持表](https://mmdeploy.readthedocs.io/en/latest/04-supported-codebases/mmpose.html)列出 RTMPose TensorRT 支持。适用于 RTMPose 路线，不自动适用于当前 SAM DINOv3；须检查关键点与评分差异。
- [Triton Inference Server](https://github.com/triton-inference-server/server)：[动态 batching](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)适合后续多用户共享无状态检测/姿态模型；跟踪状态仍须独立、有序。
- [DALI](https://github.com/NVIDIA/DALI)：更适合 Linux GPU 解码与预处理；经典 video reader 仅支持恒定帧率，不能无条件替换当前保留 PTS 的解码器。

[Cloudflare 单请求大小限制](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/)与整个视频大小不同；合理分块可以上传大文件，不代表需要为了 500 MB 总文件升级套餐。分块、轻量回执不能消除用户上行瓶颈或跨网延迟。若修复后传输依然慢，可再比较浏览器直传带签名的对象存储、服务端拉取方案，另计存储成本和访问权限；本次没有引入新云服务。

## 验证

126 项针对 Python 测试通过，覆盖上传/API、单帧与批量检测、VFR/PTS/stride/尾批绑定、取消、网格归档、诊断等价与 CPU 收尾。前端上传与网关测试、TypeScript 检查、生产构建通过。上述小样本检测基准已经运行。完整 SAM 多帧前向及整体评分性能尚未验收。

2026-11-01 17:22–17:24（北京时间）确认无运行/排队任务且上传会话无近期写入后，使用既有重启脚本重载本机 8000/8001 服务，沿用 SAM3D DINOv3。原 Cloudflare PID 47252 保留，前端 HTTP 200，API ready。上传分块默认仍为 128 KiB，检测 batch 默认仍为 1。

经正在运行的本机网页网关实际上传 5,211 字节测试视频，PUT 返回 240 字节的 `chunk-v1` 回执，GET 恢复清单的块号和 SHA256 验证通过。未调用 complete，因此没有创建推理任务。证据为 `reports/upload-inference-performance-20261101/live-upload-smoke.json`。本次未验证朋友到 Cloudflare 的公网传输速度。

自动审批拒绝删除临时测试上传会话，原因仅返回 `blocked by policy`。因此保留会话 `bb23ee13-d266-4da5-85f9-1b24fa5db3ec`（未提交、5,211 字节）；没有重试删除，也未影响服务。
