# 单机 GPU worker 操作与资格边界（2026-11-07）

当前目标是同一台机器上的本地共享队列、每张物理 GPU 一个常驻 worker 进程树。网页与 API 可以继续放在同一台机器，GPU worker 不开网络监听端口。这里的多卡结构和离线容量预测不是实际多卡吞吐验收；本轮完整视频实测及预测数值由 `sam3d-capacity-20261107.md` 单独记录。

当前相关170项CPU回归已通过；预算参数化后的smoke专项9项也已通过。真实受管理单卡队列smoke已通过，使用1024 MiB detector、10240 MiB SAM及2048 MiB物理余量；默认11264 MiB SAM cap尚未完成本机受管理实测。整片batch1/batch8计时与逐帧对照均已完成，生产仍保持batch1。以下命令用于后续维护复现；进行新的计时对照时，不得同时启动额外模型、推理或大量CPU测试。

## 运行合同

- 所有 worker 绑定完整物理 GPU UUID，`CUDA_VISIBLE_DEVICES=<UUID>` 在 Python/Torch 导入前设置，进程内使用 `cuda:0`。映射符合 [NVIDIA CUDA 环境变量说明](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/environment-variables.html)。不要使用会随设备枚举变化的物理序号作为持久身份。
- 一张 UUID 仅一个 worker 树。Windows 使用主机级 named mutex；持久 owner/quarantine 状态放在 `%ProgramData%\RallyMate\gpu-state`，与任务 data root 无关。改变数据目录不会绕过锁或隔离状态。
- 默认 detector 与独立 SAM 进程的 PyTorch 分配器上限分别为1024 MiB、11264 MiB，另要求2048 MiB物理空闲余量。两个实际 fraction 都按 `上限字节 / torch 观测 total_memory` 计算并读回核对。这些上限不限制驱动 context、OpenGL、NVENC 或其他非 PyTorch 分配，也不构成整卡硬性显存隔离。空闲显存检查仅在worker启动前执行，不是运行中的动态显存调度器；后续其他程序占用显存仍可能造成OOM。
- 当前受管理 profile 强制 SAM frame batch1、detector batch1、空 batch graph shapes、512 MiB forward/head graph预算及有界归档队列。实验 shell 中的 batch8 设置不会自动继承。batch8仍需精度、来源证据和完整行为门禁，吞吐优势本身不等于上线许可。
- worker 使用同主机本地 SQLite/WAL 和本地任务目录，空闲 worker 原子认领最早可执行任务。这里不是跨主机队列，不支持把 SQLite 放到网络文件系统后宣称分布式调度。
- managed worker 不凭过期 lease 自动重跑未知活树。正常 supervisor 停止时，先确认全部 OwnedProcess 树已退出，再按准确 worker ID 与 attempt 恢复其未完成任务。数据库的旧式过期重排与重试耗尽处理也已明确排除 `worker_id LIKE 'fleet-%'`，避免旧worker自动回收managed任务；仍建议同一队列只运行受管理fleet，避免混用两种生命周期与运维方式。

## 本地准入与权限边界

`RALLYMATE_MAX_ACTIVE_JOBS` 默认32，仅原子限制 `queued + running`。运行中已请求取消的任务仍计数，直到 worker 确认停止。超限429携带 `Retry-After: 30`；32是当前配置默认值，尚未证明是任意硬件上的最优并发或等待 SLO。

可恢复上传没有全局字节配额或占位预留；很多未完成上传可以在进入32任务上限前占用磁盘。现有内部 API key 为共享 key，job ID没有租户所有权隔离。当前可以做本地同机多用户并发队列测试，不能据此宣称已具备公开多租户服务隔离。没有授权开放公网或新增隧道。

`RALLYMATE_ACCEPT_NEW_JOBS=0` 在新的上传会话与最终任务提交入口返回503 `submissions_paused`，携带 `Retry-After: 30`。已有分片、状态和报告读取保留。健康检查的 `admission` 显示 `max_active_jobs` 与 `accept_new_jobs`。这是进程启动配置，不是热更新开关。

## 维护、单卡 smoke 与回退

以下 PowerShell 命令均在仓库根目录执行。先确认正式队列已空、计时实验已结束，并保留当前 API/网页配置。维护入口会校验现有监听进程归属，拒绝忙队列或无法核实的进程，不按进程名批量结束其他 Python。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\restart_preview_service.ps1 -ApiOnly
```

这会保留可读取报告的 API/网页模式，暂停新任务准入，不加载常驻推理 worker。已有服务的监听端口及地址保持既有受控配置。API-only本身也可以用于外部 fleet，因此底层 `run_dev_service.py --api-only` 不强制关闭准入；维护用 PowerShell入口显式设置0。

先审阅单卡计划；省略 `--gpu` 只允许当前 inventory 中恰好一张物理 GPU，否则必须给完整UUID：

```powershell
$env:PYTHONPATH = Join-Path $PWD "src"
& .\runtime\rtmpose\.venv\Scripts\python.exe .\scripts\smoke_managed_gpu_worker.py
```

计划不创建 data root、不加载模型、不提交任务。确认可以启动诊断后，显式运行：

```powershell
& .\runtime\rtmpose\.venv\Scripts\python.exe .\scripts\smoke_managed_gpu_worker.py --launch --frames 16 --sam-mib 10240 --timeout-seconds 900 --output .\.codex_tmp\managed-gpu-smoke-sam10g-recheck
```

smoke可显式设置 `--detector-mib`、`--sam-mib`、`--reserve-mib`，默认仍为1024/11264/2048 MiB。监督器把实际plan预算写入本次identity，模型读回与最终回执都按该identity核对，不将配置值伪装成硬件观测。本次实测及上面复现命令显式使用 `--sam-mib 10240`，保留1024 MiB detector与2048 MiB物理余量，总启动需求13312 MiB；实际plan中的空闲显存为14023 MiB，不能满足默认计划的14336 MiB需求。此次没有关闭用户桌面或浏览器来释放显存，也没有修改生产默认。复现必须选择不存在的新输出目录。

输出目录必须不存在，且位于本仓库 `.codex_tmp` 下；脚本拒绝复用。默认只读完成任务 `afd950a6-0967-49dc-8e74-fd1b4c76a9e6` 的 request以确定原视频位置，也可以用 `--source-request <本地request.json>` 明确指定。原视频就地只读，不复制至正式 uploads，不连接正式数据库。

监督器创建全新空DB并启动一个 OwnedProcess 树。真实模型初始化后，`runtime-ready.json` 必须含本次 worker ID、nonce、GPU UUID、detector和SAM各自实际 allocator readback，才创建一条16帧任务（可选8帧）。随后走生产 wrapper → `run_worker` → 原子队列认领 → `PersistentVisionRunner` 路径，要求任务成功、连续源帧不缺失，且有至少一个成功的原生SAM主体。首次预测后再记录 `runtime-after-job.json`，确认 YOLO权重确实位于逻辑 `cuda:0`，原生输出的视频SHA、PTS/timebase、主体与epoch匹配当前原始帧。

这是**单卡受管理队列烟雾测试**，不是吞吐、模型精度或三维渲染资格验证。smoke专用关闭mesh导出/归档/渲染和标注视频，避免写共享mesh缓存；正式服务默认不受影响。模型仍读取现有固定权重；日志、IPC、临时文件和任务产物写入新诊断目录。任何失败、超时或Ctrl+C都会进入同一停止流程。

本次证据保存在 `.codex_tmp/managed-gpu-smoke-sam10g-20261107/` 的 `plan.json`、`runtime-ready.json`、`runtime-after-job.json` 和 `receipt.json`。物理设备为 RTX 5070 Ti，UUID为 `GPU-ea77601b-a3e3-4c2e-f8c7-9a0379220417`；worker为 `fleet-36fb276a3d6943c180dee400d757a8c2`，隔离任务为 `9b4273b9-0cfe-435e-ba05-2f7e76a3956d`。相对监督器开始计时，模型就绪18.625秒，任务成功24.031秒，整树停止及最终检查完成24.328秒。它们包含模型加载等启动成本，不是16帧稳态推理耗时。

16个连续源帧全部保留，16条原生主体记录均为 `ok`，来源视频SHA为 `fee3364086c03392181100379b31e37b3eed4cf332eae5f575543c43e02b677f`。detector在首次预测后实际位于 `cuda:0`，当前fraction为0.06281221127046745；独立SAM进程在模型加载前读回fraction为0.6281221127046744。两个进程均观测同一物理UUID和逻辑 `cuda:0`，按观测总显存17094475776字节核对1024/10240 MiB cap。SAM值是其初始化回执，不是另一次推理后的实时查询；完整原生输出同时证明本次任务已执行。

最终 `receipt.json` 为 `passed`，`tree_exit_confirmed=true`、`gpu_quarantine_cleared=true`，没有待恢复attempt。隔离DB仅有本次1条 `succeeded`，隔离uploads为空，原视频size/mtime检查未变化。此次证明10 GiB SAM cap下的单卡启动、队列任务、来源绑定和停止链路成立；不扩展为默认11 GiB cap、多卡吞吐或模型精度结论。

最终只接受 `receipt.json` 的 `status=passed`，且 shutdown中对应worker的 `tree_exit_confirmed=true`、队列只有本次成功任务、uploads为空。`worker_launched`、`worker_state=ready`、单个launcher PID退出或等候一段时间都不是完整验收。receipt记录的隔离标记清理结果可以为false（例如worker已正常清理）；它不能替代树退出确认。

正常停止由 `stop_managed_workers` 统一负责，不得先在外部 `owner.close()` 再调用该接口。若树退出未确认，保留 owner/quarantine 和诊断证据，不自动重试、不删除标记文件。父supervisor崩溃后，也不能用“旧PID不存在”判定整树已安全停止。人工修复必须有针对原 OwnedProcess 树的退出确认；没有确认就维持隔离。不要写通配符删状态或扫描PID后误杀复用PID的脚本。

确认smoke所有树停止之后，回退到原默认单卡预览服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\restart_preview_service.ps1
```

此入口恢复默认准入1和默认常驻worker。先核对 `/health/ready` 的 admission与队列，再进行正常任务测试；若quarantine仍存在，修复原因并保留证据，不能为了恢复页面而绕过隔离锁。诊断输出保留，不自动删除。

## 多卡试验的启动与停止

多卡只在相应硬件到位并完成平台检查后试验。先查完整UUID并做只读计划：

```powershell
nvidia-smi --query-gpu=uuid,name,memory.total,memory.free --format=csv,noheader,nounits
& .\runtime\rtmpose\.venv\Scripts\python.exe .\scripts\run_gpu_fleet.py --gpus "GPU-<完整UUID1>,GPU-<完整UUID2>" --data-root .\.codex_tmp\fleet-qualification-data
```

真正启动需要额外加 `--launch`；保持监督器存活，并用Ctrl+C进入其正常停止流程。它将打印每个worker的树退出与准确attempt恢复回执。该启动器本身不启动API，不打开监听端口。不要拿仅计划输出或两个伪造UUID单元测试当作真实双卡运行证据。

同机网页/API与fleet共享同一绝对 data root及database path时，必须由API-only模式服务，避免额外启动旧默认GPU worker。保持已审阅的模型、registry和内部key配置，明确以 `RALLYMATE_ACCEPT_NEW_JOBS=1` 启动API-only进程后才允许测试提交；维护入口 `-ApiOnly` 总是暂停准入，不能误以为启动fleet会自动打开提交。完成测试后先停止fleet并确认其所有树，再恢复默认单worker服务。本文不提供扩大监听地址或公网部署步骤。

## 共享主机容量与待测项目

每worker当前 detector Torch intra-op预算2、SAM intra-op/interop预算1，读回实际线程池变化；这不是每卡“只占2核”的硬配额。解码、跟踪、CPU后处理、线程库、JSON证据构造、磁盘归档与网页请求仍会竞争主机资源。要为API摘要/动作请求和交互留出CPU余量，不仅按GPU数量堆进程。

RAM要观察整个树的RSS、private commit及系统available/commit，而不是把独立进程RSS简单相加后当作物理总占用。整片证据位于 `.codex_tmp/batch-execution-20261107/full-13116/host-memory.jsonl`，汇总见同目录 `result-summary.json`。377次采样从实验开始后约371.5秒延续到逐帧比较结束；该观测区间内树RSS最大10.43 GiB（11201708032字节）、private commit最大19.40 GiB（20829876224字节），系统available最低32.13 GiB（34498949120字节）。RSS求和可能重复计入共享页，树指标不含API/UI；采样缺少启动及前约6.2分钟，所以这些值不是全程峰值，也不能直接乘卡数当作主机RAM需求。

整片单卡实验的1秒GPU采样共4108点，全物理卡观测峰值9697 MiB，范围包含加载、预热、两次trial和比较，以及桌面/驱动显存。该实验配置与受管理smoke不同，不用于替代managed allocator资格验证。NVMe仍需观察容量、剩余空间和同时读原视频/写native帧、证据池、mesh原始数组时的延迟与吞吐；分片上传配额当前未实现。

managed模式暂停自动mesh渲染，等待独立设备路由和资源调度资格验证。CPU、NVMe、显存与OpenGL/NVENC都可能受mesh影响，不能把不含mesh的模型吞吐直接当作“报告和三维都完成”的用户端时延。

多卡平台还需PCIe lane与插槽间距、CPU/NVMe共享带宽、主板/固件、PSU持续供电及瞬态、散热和机箱空间验证。不能仅凭每卡显存推算某一固定主机配置或购买建议。1/2/4/8卡离线FIFO结果假设无共享CPU/RAM/NVMe/PCIe折损、模型已热，不包括上传/启动/网页交付/独立mesh；真实多卡排队与完成分位数必须实测。

| 资格项目 | 当前状态 | 证据与边界 |
| --- | --- | --- |
| 单卡managed启动/首预测/源绑定/退出 | 已通过，限1024/10240/2048 MiB配置 | 16帧16条成功原生记录；ready18.625秒、成功24.031秒、整树停止确认；默认11 GiB SAM cap未实测 |
| 整片batch1与batch8单卡对照 | 两次单trial已完成，非重复性结论 | 13116帧；2212.724秒对1693.840秒；逐帧来源/路由及评分决策相同，batch8数值证据非逐位相同；详见容量文档，生产仍batch1 |
| 主机RAM与commit观测范围 | 377次中途开始采样已汇总 | 树RSS10.43 GiB、commit19.40 GiB；system available最低32.13 GiB；缺启动/前约6.2分钟，不称全程峰值 |
| 2/4/8张实际GPU并发 | 未实测 | 每UUID身份、每worker job/attempt、全树资源、端到端排队/完成分位数 |
| CPU/NVMe共享折损及上传配额 | 未完成 | 满载多视频并发、磁盘低空间处理、配额/预留设计 |
| 独立mesh路由及调度 | 未完成，managed自动mesh关闭 | 设备映射、显存和CPU/NVMe竞争、三维完成SLO |
| 公开多租户隔离 | 不在本地队列范围 | 身份、作业所有权、上传/存储配额和公开部署的单独设计与授权 |
