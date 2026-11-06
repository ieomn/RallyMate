# SAM BODY 多帧批量推理：实现与验证

日期：2026-11-01。对应上传与推理性能排查的后续实现。

### 2026-11-02 GPU 数据流水线第一阶段

新增 `RALLYMATE_SAM3D_BATCH_PREPROCESS=1` 实验开关，默认关闭。`sam3d_batch_preprocess.py` 保持官方 OpenCV 裁剪、相机与仿射元数据，将最多 8 个人物的 uint8 裁剪图合成一次 H2D 上传，在 GPU 转 float32 并归一化。对全部 256 种 uint8 值验证归一化；若 GPU 除法不能逐位匹配官方 CPU 值，使用官方值查表。

host pinned 和 device uint8 缓冲固定复用，各自容量最多 24 MiB；最终 float 模型输入独立分配，不能将 staging 上限理解为总显存上限。CUDA 事件覆盖首次分配的流依赖、前次 H2D 完成后才能重填 host、前次读取完成后才能覆盖 device，以及当前 H2D 完成后才启动计算。关闭 executor 时等待本实例事件后释放缓冲。

已有单帧 GPU transform 本来就会逐人物上传 uint8 crop；本次改善点是将 N 次上传合为 1 次，并复用 staging，不能描述为从全 CPU 预处理直接迁移到 GPU。仿射裁剪和 RGB 转换仍在 CPU，runner 前后同步及输出回传仍存在，也没有实现跨批模型前向重叠。

benchmark 新增 `--batch-preprocess 0/1`，显式参数优先于环境；回执强制验证确实单次上传、真正单次批前向。所有 70 点、完整 mesh、相机、焦距、拓扑与来源绑定均参与比较。61 项 CPU 测试在 Windows 与 WSL Ubuntu 通过；实际 GPU 收益与端到端结果以 `reports/upload-gpu-mainline-20261102/` 的后续报告为准，测试通过本身不代表提速或生产已启用。

WSL 下额外执行了真实 CUDA 小张量校验：16×16 crop，人数依次 8/3/8/1，全部 256 种 uint8 值逐位匹配官方值，host/device 地址复用且此前 float 输出没有被覆盖，事件等待和关闭正常。本机实际采用 GPU 查表归一化。证据为 `linux-cuda-staging-smoke.json`；此项未加载 SAM，也没有测量性能。

**Windows 真实 SAM 对照：**320×568 单人视频的同一 16 帧（33–48），graph8、blocking-sync、每个独立 worker 三轮单帧/批次交替，worker 顺序为预处理 off/on/on/off。单帧中位数约 12.0–12.2 FPS，batch8 约 18.4 FPS。新 staging 的两轮 worker 中位数为 18.370/18.482 FPS，旧 batch 路径为 18.384/18.446 FPS；合计差异约 +0.06%，不足以证明额外提速，因此继续默认关闭该开关。真 batch 本身的吞吐改善与新 staging 的收益必须分开报告。

跨 worker 的所有批次均保留来源顺序、70 点和完整 mesh；最大二维差异约 0.000290 像素、三维约 7.60e-7、顶点约 8.49e-7 模型单位，focal/faces 完全一致。数值并非逐位相同，不是评分准确性验收。staging off/on 的 worker allocated 峰值约 4.101/4.106 GiB、reserved 约 5.590/5.609 GiB。报告为 `reports/upload-gpu-mainline-20261102/gpu-staging-comparison.json` 及四份原始 `gpu-staging-*.json`；计时含 crop/H2D/模型/D2H/IPC/mesh capture，排除加载、预热、完整 pipeline、上传和视频渲染。

**128 帧完整分析回归：**960×544 素材、最多双人、实际 238 个人物请求、检测 batch1，分别对 4 帧和 8 帧配置各运行 ABBA。4 帧试验的 pipeline 中位数为单帧 27.584 秒、批次 23.837 秒，约少 13.6% 耗时（约 15.7% 吞吐提升）；8 帧试验为 27.712→23.930 秒，收益接近。两组每轮均 238/238 成功，来源/身份/相机路由、12 个事件核心字段和 52 条评分决策一致；完整网格均参与比较。记录仍有数值和证据差异，重复单帧也存在类似差异；39 条评分为 calibration_required、13 条 unavailable，没有借此声称正式评分准确性通过。

报告分别在 `reports/upload-gpu-mainline-20261102/pipeline-128frames/` 与 `pipeline-128frames-batch8/`。计时包含完整分析/归档/评分与报告收尾，排除模型加载、预热、上传、标注视频及后台 mesh 视频渲染。`scripts/benchmark_sam3d_pipeline.py --batch-frames {4,8}` 可复现，默认 128 帧、上限 512。新的 GPU staging 继续关闭，当前公共生产 SAM 默认仍为单帧；本机显式批配置入口为 `scripts/restart_performance_preview.ps1 -SamBatchFrames 8`，具体推广限制见上传性能文档的本轮部署部分。

## 当前状态

已经实现真正的多帧 BODY 批推理，并在本机 RTX 5070 Ti 上执行真实模型验证。不是把逐帧调用放进多个线程：同组人物 crop 组成 `B=N, P=1`，调用一次官方 `model.run_inference`，再按原始帧和人物拆回结果。

生产默认仍为单帧。本次开发与基准使用隔离进程，尚未把正在运行的 Cloudflare 入口服务改成批推理。小样本数值一致性不等于完整视频事件和评分验收，更不能代替朋友的 500 MB 视频公网测试。

## 数据流与边界

1. 按原始 PTS 解码，保存每帧时间戳、帧号及像素引用。
2. 检测可使用已有独立批次开关；tracker、相机运动、主体锁定和初始主体缓冲仍按时间顺序执行。
3. 身份确定后，组装最多 8 帧的 SAM 请求。每帧保留自己的原图、bbox、相机参考 epoch、人物 track ID 和 selection epoch。
4. IPC 使用独立共享内存槽传递每帧原图；非共享内存模式仍支持临时 NPY。请求编号必须匹配，一次只允许一个在途请求。
5. worker 将有效人物展平，按人物上限分组，每组最多 8 人。保留官方逐图 crop、相机内参与仿射变换，合并张量后一次前向；仅剩一人时使用现有单人路径。
6. 输出全部原生 70 关键点和完整人体网格，按请求顺序恢复帧槽，依次归档、写 JSONL、评分。空人物、无效框和失败槽均有明确状态。

没有修改模型权重、分辨率、浮点精度、关键点定义、抽帧策略或评分公式。当前仅支持官方默认 FOV 的 BODY 模式；full/hand、多线程共享同一个模型和跨任务动态组批不属于本次实现。

关键代码：

- `src/rallymate_vision/sam3d_batch.py`：校验固定版本源码；组装官方批输入并执行一次前向。
- `src/rallymate_vision/sam3d_runner.py`：批 IPC、人物微批次、原生输出校验与 worker 生命周期。
- `src/rallymate_vision/sam3d_adapter.py`：逐帧身份准备和有序输出解包。
- `src/rallymate_vision/sam3d_pipeline_batch.py`、`pipeline.py`：有限缓冲、顺序发布、取消与归档。
- `src/rallymate_vision/sam3d_cuda_graph.py`、`sam3d_forward_graph.py`：批尺寸对应的独立 CUDA Graph。

## 为什么仅把 N 调大不够

原有 CUDA Graph 为单人物形状捕获。直接使用两帧 batch 会退出单人图优化，在最初测试中比原先单帧还慢。新增实现保留原单帧图，另外按指定人物数捕获 body head、backbone 和 decoder layer 的固定形状图。

每种形状都有独立输入/输出缓冲、状态与回执。batch=1 在批推理前后仍可使用原图；不同形状不会错误地重放单人图。官方 decoder 的 `context_pe` 允许前导维为 1 并广播，只有这一已验证参数允许此例外。

最多额外缓存两种形状。body head 和 forward 模块各有 512 MiB 的额外图保留预算；后者由 backbone 与各 decoder layer 共享。可用显存不足或保留预算超限的图保持 eager 执行，并记录原因。预算限制的是观测到的保留分配/预留增量，不是整个进程显存或捕获瞬间峰值的硬上限。

实测同时配置 `2,4` 时，形状 2 可先占满 forward 的预算，导致形状 4 无法捕获。因此固定单人视频优先只缓存实际要用的形状。图捕获有冷启动成本，短视频未必受益。

## 内存、失败和计时

- pipeline 的 SAM 待处理原图预算为 64 MiB；超大单帧独立处理。解码预取、检测、主体初始化各有独立缓冲，不能把这个数字当成进程总内存上限。
- IPC 每批最多 8 帧、原图总量最多 128 MiB。共享内存池同时限制保留容量，跨任务遇到不同尺寸时不会无限累积；子进程在回执前释放这一批映射。
- worker 每帧最多接收 4 个人物框，实际前向按 `BATCH_PERSONS` 再分组。8 帧、每帧 2 人、人物上限 8，需要两次前向。
- 输出在发布前校验数量、70 点维度、有限值、相机与焦距；失败保留所有输出槽。不会对失败批次自动逐帧重跑，避免掩盖 OOM 或产生重复结果。
- 正常取消或批次部分捕获失败时，回收尚未发布且校验归属与 SHA256 的本次 mesh 文件；保留共享拓扑、已归档文件和已交给异步归档器的槽。不会扫描或删除历史缓存。
- 超时、错误回执或取消终止 worker 时，先停止其进程树，再释放共享内存，避免旧 worker 读取被覆盖的帧。
- 每条原生记录的 `inference_ms` 是该人物分摊的批次墙钟耗时；`batch_execution.wall_ms` 与 IPC 计时属于整批，不能跨记录相加。pipeline 的姿态计时按实际调用累计。

## 配置

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `RALLYMATE_SAM3D_BATCH_FRAMES` | `1` | pipeline 每批源帧数，1–8 |
| `RALLYMATE_SAM3D_BATCH_PERSONS` | `4` | 每次模型前向人物数上限，1–8 |
| `RALLYMATE_SAM3D_BATCH_GRAPH_SIZES` | 空 | 额外捕获的人物批尺寸，例如 `8`，最多两个 2–8 的不同值 |

批图要求 BODY、body-head graph 和 rotation 优化开启。backbone/decoder layer 的批图还需要现有 `RALLYMATE_SAM3D_FORWARD_GRAPH=backbone_decoder_layers`。DINOv3 既有启动脚本会设置这些已验证的单帧基础优化；仅设置 `BATCH_FRAMES=8` 而保留 `BATCH_PERSONS=4` 会成为两次 4 人前向。

单人视频的 8 帧候选配置（**这是复测配置，不表示已部署**）：

```powershell
$env:RALLYMATE_SAM3D_BATCH_FRAMES = '8'
$env:RALLYMATE_SAM3D_BATCH_PERSONS = '8'
$env:RALLYMATE_SAM3D_BATCH_GRAPH_SIZES = '8'
```

环境变量必须在 worker 创建前传入；已有模型进程不会随 shell 环境变化。正式切换须使用现有空闲检查与重启流程。回退时设 `BATCH_FRAMES=1` 并清空 `BATCH_GRAPH_SIZES` 后重新启动 worker。

## 验证证据

基准脚本 `scripts/benchmark_sam3d_batches.py` 从真实原生档案读取 bbox 和帧绑定，对源视频 SHA256、PTS 和尺寸逐项核对，再解码完全相同的帧。交替执行单帧与批次配置，模型加载、预热和结果比较不计入稳态吞吐；计时包含 IPC、原生输出和完整 mesh capture，不包含上传、检测、跟踪、评分和渲染。运行前及每批检查服务队列空闲。

输出逐项比较：70 个二维与三维关键点、18,439 个顶点、相机平移、焦距及面片拓扑。只报告数值差异，不把 `all_exact=false` 隐藏成完全一致。重复单帧自身也存在很小的浮点差异。

**姿态阶段基准：**DINOv3 模型，320×568 单人视频的 8 个连续源帧，batch1 与候选各交替 3 次，取吞吐中位数。两行使用各自独立的 worker 和单帧对照。

| 配置 | 对照单帧 FPS | 批推理 FPS | 吞吐提升 |
| --- | ---: | ---: | ---: |
| 4 人/前向，仅配置 graph4 | 13.450 | 17.783 | 1.322× |
| 8 人/前向，仅配置 graph8 | 13.671 | 20.587 | 1.506× |

batch8 的该 worker CUDA allocated 峰值为 4.10 GiB、reserved 峰值为 5.59 GiB；不包含常驻生产服务的独立模型、父进程 RAM，不能视为整卡占用。单人批测最大二维关键点差异小于 0.0003 像素，三维和顶点差异小于 1e-6 模型单位，焦距与 faces 拓扑完全一致。单人 graph8 的预热另耗时约 1.90 秒，不能用稳态吞吐外推极短任务。

**完整分析流程小样本：**另一段 960×544 双人视频，16 帧、32 个人物请求，固定检测 batch1，对比 SAM 逐帧与 4 帧/8 人一组；同一预加载模型按单帧/批次/批次/单帧顺序跑 4 轮。

- `run_pipeline` 墙钟中位数从 **4.188 秒降到 3.500 秒**，耗时少约 16.4%，吞吐提高约 19.7%。SAM 阶段从 **2.446 秒降到 1.746 秒**。
- 每轮 32/32 人物成功，0 个失败或无效来源绑定。源视频 SHA256、PTS、尺寸、检测结果、主体身份和相机状态一致；3 个事件的 ID、边界、关键阶段和来源窗口一致。
- 此计时包含检测、跟踪、SAM IPC、70 点、每人完整 18,439 顶点网格、归档收尾、诊断、事件、指标与 HTML 报告；排除模型加载/预热、标注视频、后台 mesh 视频渲染、上传与 Cloudflare 传输。因此仍不是朋友一次上传到全部视频完成的端到端成绩。

**评分差异没有被忽略：**13 项指标均为 `calibration_required`，没有正式 A–E 分数可用于精度验收。指标状态、规则和质量门控一致，但部分特征值与证据帧不同：两个批次试验分别有 11、10 项 feature source-frame 集合变化，重复单帧也有 10 项变化。观察到的批次最大特征差异约为 0.04505 deg/s，来自肩髋角速度变化量；FS09 的部分证据帧并集随之变化。这说明微小浮点差异可能被求导或极值选帧放大，不能宣称评分证据完全等价，暂不默认启用。

原始证据和复现命令：

- `reports/sam3d-batching-20261101/validation-summary.json`：简明汇总、测量范围、精确复现命令。
- `reports/sam3d-batching-20261101/graph4-8frames.json`、`graph8-8frames.json`：各轮真实 SAM 基准与逐人物几何差异。
- `reports/sam3d-batching-20261101/pipeline-probe.json`：完整流程比较、每项特征和证据差异。
- `reports/sam3d-batching-20261101/pipeline-16frames/`：4 轮完整原生档案、网格、事件和评分记录。

回归检查：主测试集运行 330 项，其中 319 项通过、11 项因主运行时未安装 RoMa 跳过；随后在 SAM 专用运行时执行 RoMa 13 项，全部通过，补齐这 11 项。覆盖真 crop 组批、一次前向、多人/空帧/尾批、VFR/PTS、共享内存跨进程复用与上限、协议错误/超时/取消、网格回收和异步归档、图隔离/预算/失败恢复、单帧兼容和配置接线。日志为 `regression-tests.log`、`roma-runtime-tests.log`；源码语法和 diff 空白检查通过。

## 后续推广验收

在默认启用前，固定相同视频、帧范围和检测配置，对多种长度、尺寸、人数进行完整对照，检查主体和 epoch、事件类别/时间、评分输入与分数、模型失败率、总体时间、内存及显存。图缓存预算可先保持保守，再依据实测调整。GPU 利用率只是诊断信号，主要指标是每帧正确性、整段吞吐和资源峰值。

上传优化属于前一轮工作，详见 `docs/upload-inference-performance-20261101.md`；本轮 SAM 批推理不会提高朋友的上行带宽，也不会消除评分或渲染的 CPU 成本。
