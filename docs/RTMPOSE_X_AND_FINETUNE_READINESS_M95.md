# M95 RTMPose-X 影子候选与可审计微调入口

## 当前结论

M95 已把 `rtmpose-x-halpe26-384x288-m95-shadow` 登记为单独的离线影子候选，并完成一次本机 GPU 最小烟测。它能在当前 MMPose 运行环境中加载，在三段开发视频各取一个冻结检测帧，共对 4 个 Player ROI 返回 4 组 Halpe26 结果；26 点形状和有限数检查全部通过。

这不是准确率实验。当前生产默认仍是 `rtmpose-m-halpe26-online`（RTMPose-M、Halpe26、256×192），M94 的 `models/rtmpose/deployment-presets.json` 没有被改写，X384 也没有成为 HTTP 请求可选 preset、没有被自动路由、没有晋级 F3/F4、没有生成 A～E。

M95 同时补齐了“有真人真值以后才能启动”的本地微调软件链：

1. 严格审计双人独立标注、第三人裁决、源文件哈希、26 点覆盖和数据治理；
2. 只有审计通过才导出 MMPose 可读的 Halpe26 COCO 数据集；
3. 只有数据集、模型、配置、拓扑和训练/验证隔离全部复核通过，才允许显式启动 MMEngine Runner；
4. 默认只做 dry-run，不训练，也不把训练启动或配置通过写成效果提升。

当前真实审计结果仍是 `annotation_required`：接受的人工关键点帧为 0，接受的关节值为 0，因此没有导出真实训练集、没有启动训练、没有产生新 checkpoint。

## 1. 候选身份与不可变输入

候选注册表：`models/rtmpose/m95-shadow-candidates.json`

- registry：`rtmpose-shadow-candidates-2026-09-04.1`；
- candidate：`rtmpose-x-halpe26-384x288-m95-shadow`；
- checkpoint：`models/rtmpose/rtmpose-x_halpe26_384x288.pth`；
- checkpoint 大小：200,397,852 bytes；
- checkpoint SHA-256：`7FB6E239601082A06CA8442FEB7A9774B8D77A1C37911B2AD0F27C1E84BE4D21`；
- config：`runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/body_2d_keypoint/rtmpose/body8/rtmpose-x_8xb256-700e_body8-halpe26-384x288.py`；
- config SHA-256：`961E5704E4983F27173BC008C17F08E8C2C5907FDB104EA8669AD06C4C68B678`；
- 原生拓扑：Halpe26；输入尺寸：384×288；运行档位：`analysis`；
- 角色：`offline_shadow_only_not_accuracy_validated_not_F4`。

M95 注册表还按原始字节绑定 M94 部署注册表 SHA-256 `A95B7B5F225C025371157C22A9A60397874DE03C85B4EAC512B3B9C443F14C69`，并显式声明默认 preset 仍是 `rtmpose-m-halpe26-online`。这样 X 候选证据可以继续演进，又不会重写 M94 已冻结的线上部署事实。

该文件是**烟测开始前冻结的输入/协议快照**，不是会在运行后原地改写的当前状态表。因此候选内的
`promotion_status=candidate_downloaded_not_smoke_validated_not_accuracy_validated_not_promoted` 表示注册时
状态；烟测后的权威状态是绑定该 registry SHA 的 `smoke-report.json.status=smoke_passed_ground_truth_required`，
最终汇总再由 M95 field-change record 同时保存“注册时状态”和“当前烟测状态”。保留这两层可以重放先冻结
协议、再执行烟测的顺序；不能把旧 registry 原地改成“已烟测”后继续声称旧报告仍绑定它。

## 2. 为什么选择 X384 做下一候选

[OpenMMLab 官方 Halpe26 RTMPose 模型表](https://github.com/open-mmlab/mmpose/blob/main/configs/body_2d_keypoint/rtmpose/body8/rtmpose_body8-halpe26.md)在同一公开评测表中给出：

- M256：PCK@0.1 94.75，AUC 71.91，1.95G FLOPs；
- M384：PCK@0.1 95.15，AUC 73.56，4.37G FLOPs；
- L384：PCK@0.1 95.56，AUC 74.38，9.40G FLOPs；
- X384：PCK@0.1 95.74，AUC 74.82，17.29G FLOPs。

因此 X384 是当前同 Halpe26 拓扑、改动面最小且官方同表指标最高的本地候选。它对 L384 的公开增量只有 PCK +0.18、AUC +0.44，计算量约为 L384 的 1.84 倍；是否值得这笔成本必须由 RallyMate 真人真值和相同输入条件的完整比较回答。

这些公开数字来自外部数据集，不是 RallyMate 视频准确率，也不能用来替代本项目的关键点误差、事件误差、下游特征误差或教练验证。

## 3. 本机烟测做了什么

机器报告：`reports/m95-rtmpose-x-shadow/smoke-report.json`

烟测使用三段开发视频的冻结 Player 检测框，每段只选择一个样本帧；密封保留视频 `c235…` 没有打开。实际结果：

- 3 次模型调用；
- 4 个 Player ROI；
- 4 个 Pose 返回；
- Halpe26 形状与有限值：全部通过；
- checkpoint 加载：0.498207 秒；
- 调用延迟：均值 26.646167 ms，P50 20.7584 ms，P95 37.90988 ms；
- CUDA 峰值 allocated：262,256,128 bytes；reserved：314,572,800 bytes。

延迟来自 3 个样本调用，且不同调用的 ROI 数不同，只是本机可运行性的描述，不是 M/L/X 公平性能基准。该报告明确保存：

- `RallyMate_accuracy_improved=false`；
- `ground_truth_accuracy_measured=false`；
- `candidate_promoted=false`；
- `production_default_changed=false`；
- `sealed_holdout_opened=false`。

## 4. 为什么仍不允许微调

就绪审计实现：

- `src/rallymate_training/pose_finetune_readiness.py`；
- `scripts/audit_pose_finetune_readiness.py`；
- 报告：`reports/m95-pose-finetune-readiness/readiness.json`。

当前报告版本为 `pose-finetune-readiness-v1.2.0`，文件 SHA-256 为
`397DEC0D11E898C8E61ACA15B94BE6682A1127A30A254071C413D921627A8223`，输入 fingerprint 为
`06DC8DA8A57763E18F11263467A48AE6A8EA0410DE07E62F19D7840724FEAD8D`。报告同时绑定 M95 shadow
registry 的原始字节 SHA-256 `11B656E536964E8D84415719D82CE5BB5451C4071CDEA295211962323AB2E81B`；
候选注册表若改变，审计会关闭，必须显式升级版本、固定 SHA 和测试后再使用。

当前审计的两个严格来源包均没有接受的人工关键点：

- `data/annotations/small-roi-keypoint-truth-halpe256-v1`：1,834 个任务，0 个接受帧，0 个接受关节值；
- `data/annotations/event-bounded-pose-gap-truth-m75-v1`：142 个任务，0 个接受帧，0 个接受关节值。

两包合计 1,976 个待完成人工任务。现有模型预测、候选关键点、自动框和可测实例都不能冒充人工训练标签。现有任务也没有覆盖完整的 26 点，因此即使只补完旧表，仍不足以直接获得完整 Halpe26 训练资格；需要按固定协议创建或扩展完整 26 点任务。

审计只有同时满足以下条件才返回 `ready_for_dataset_export`：

- 输入包与 compiled 产物路径、版本和 SHA 全部一致；
- 每个 compiled validation report 的状态精确为 `ready_for_keypoint_error_evaluation`；
- 每个不可变任务都有两名不同标注者的独立意见；
- 裁决者与两名标注者不同，裁决结果与编译结果逐字段一致；
- 训练集内 26 个 Halpe26 关节均具有至少一条人工可见坐标监督；
- 每个源视频按原始 SHA 绑定；相同源视频不能通过改名跨 train/val；
- 治理 CSV 的 `video_id,subject_id,session_id,camera_id,consent,license,split` 七个字段完整；
- 治理 `split` 只接受精确小写 `train` 或 `val`，开发阶段的 `test` 直接阻断；
- train/val 按源视频、运动员和拍摄 session 隔离；任何两个 `video_id` 也不能绑定同一 resolved path 或视频 SHA，即使它们被分到同一个 split；
- 同一 `video_id/source_frame_index` 的人工关节不能拆在多个 truth pack 中再静默合并；
- M95 注册表中的密封保留视频不能以 ID、路径或相同内容 SHA 的别名进入开发数据；
- 没有重复任务、重复帧/关节、路径漂移、非有限值或未解释的不可见点。

## 5. COCO/Halpe26 数据集导出

导出实现：

- `src/rallymate_training/mmpose_dataset.py`；
- `scripts/export_mmpose_halpe26_dataset.py`。

导出器在抽帧前后各执行一次就绪审计，并要求输入 fingerprint 完全相同。它按源视频精确读取原始帧、复核 JPEG 尺寸，生成：

- `annotations/train.json`；
- `annotations/val.json`；
- `images/*.jpg`；
- `image-manifest.jsonl`；
- `manifest.json`。

COCO `keypoints` 始终保持仓库 Halpe26 registry 的 26 点顺序。人工可见且有坐标的点写 `v=2`；人工明确不可见或该帧未标注的点写 `v=0`，坐标为 0。扩展状态数组保存精确 wire value：`adjudicated_visible_coordinate`、`adjudicated_invisible_no_coordinate`、`not_annotated`；语义分别是“裁决后可见并有坐标”“裁决后不可见无坐标”“本帧没有该点的人工任务”。用于 top-down crop 的 task bbox 会保留来源标记并明确 `bbox_ground_truth_claim=false`，不会把检测框冒充关键点真值。

开发导出只允许非空 train 和非空 val，不生成 `test`。annotations JSON、image-manifest JSONL 与 JPEG
均列入 dataset manifest 的 artifact 清单并记录路径、字节数和 SHA-256；manifest 自身由训练适配器必填的
`dataset_manifest_sha256` 参数绑定。目标目录已存在、抽帧期间来源变化、哈希不符或任何门禁失败时，整个输出不会落地。

此外，readiness 先要求 train 和 val 的每个候选导出帧至少有一个人工可见关键点，且可见归一化坐标位于
半开区间 `[0,1)`；exporter 再使用不可变帧尺寸映射像素，要求 `x < width`、`y < height`，并检查点位于
该任务的 top-down bbox 内；adapter 对导出结果再次复核。这样
exporter 写出 `ready_for_mmpose_training` 后，训练适配器不会再因为同一组字段的不同解释而二次拒绝。
抽帧完成后还会比较 train/val 的 JPEG 原始字节 SHA 和按 MMPose 同样三通道规则解码后的像素 SHA；
即使同一画面使用不同 JPEG 编码，也不能跨 split 伪装成两张不同图片。

当前因为就绪审计为 `annotation_required`，真实导出命令会按设计拒绝，尚不存在可用于训练的真实 M95 dataset manifest。

## 6. RTMPose-X 本地训练适配器

实现文件：

- `training/configs/rtmpose_x_halpe26_384x288_rallymate.py`；
- `src/rallymate_training/mmpose_finetune.py`；
- `scripts/run_rtmpose_finetune.py`。

该适配器独立于原有 Ultralytics YOLO 训练引擎，不改变旧接口。它只接受 `rallymate-mmpose-halpe26-dataset-v1.0.0` manifest，并重新计算：

- manifest 与全部 artifact 的 SHA/字节；
- Halpe26 registry 的顺序和 SHA；
- train/val COCO 内容、图片、来源视频和治理字段；
- video/subject/session 以及源路径/内容 SHA 隔离；
- 密封保留视频未被使用；
- 固定 X384 base config、checkpoint 和训练模板 SHA；
- 当前 MMPose runtime 能解析 resolved config 并构建数据集。

训练模板采用小批量 AMP、固定随机种子、40 epoch 上限、PCK/AUC 验证和 validation alias；它没有 test/holdout 调参入口。未传 `--execute` 时只写不可覆盖的 resolved config 与 dry-run 记录。显式 `--execute` 也必须在固定 RTMPose Python 环境中运行，只有全部门禁通过才调用 `mmengine.Runner`。

运行记录不会把“准备调用 Runner”误写成“已经训练”。execute 先记录
`validated_ready_to_start(training_started=false)`，Runner 构造完成后记录
`runner_initialized(false)`，只有 Runner 进入 `before_train` 才记录 `training_running(true)`；完成、初始化
失败、启动失败、训练失败和相应中断状态都会单独保留。对 PyTorch 新版本所需的
`weights_only=false` 只允许精确 SHA/大小重新校验后的批准 checkpoint，其它文件不能借用该例外。

配置解析、dry-run 或训练启动都不构成准确率结论；新 checkpoint 还需要冻结验证协议和独立保留集评测，才能讨论是否登记为部署 preset。

## 7. 当前与未来的运行顺序

当前可重放的只读审计：

```powershell
$env:PYTHONPATH = "$PWD\src"
python .\scripts\audit_pose_finetune_readiness.py `
  --pack .\data\annotations\small-roi-keypoint-truth-halpe256-v1 `
  --pack .\data\annotations\event-bounded-pose-gap-truth-m75-v1 `
  --output .\reports\m95-pose-finetune-readiness\readiness.json
```

只有真人标注、裁决和治理 CSV 全部完成后，才运行数据导出：

```powershell
python .\scripts\export_mmpose_halpe26_dataset.py `
  --pack <完整Halpe26真值包1> `
  --pack <完整Halpe26真值包2> `
  --governance <治理CSV> `
  --output <新的不可覆盖数据集目录>
```

先做 dry-run；操作者确认 manifest SHA 后才能显式训练：

```powershell
runtime\rtmpose\.venv\Scripts\python.exe .\scripts\run_rtmpose_finetune.py `
  --dataset-manifest <dataset\manifest.json> `
  --dataset-manifest-sha256 <MANIFEST_SHA256> `
  --output-dir <新的不可覆盖run目录> `
  --dry-run

runtime\rtmpose\.venv\Scripts\python.exe .\scripts\run_rtmpose_finetune.py `
  --dataset-manifest <dataset\manifest.json> `
  --dataset-manifest-sha256 <MANIFEST_SHA256> `
  --output-dir <新的不可覆盖run目录> `
  --execute
```

当前不应执行后两段命令：真实 dataset manifest 尚不存在，门禁会拒绝。

## 8. 后续有效性验证

完成真人真值后，下一轮应冻结 M256、L384、X384 的 checkpoint/config、同一 ROI、同一帧和同一后处理，对完整开发分层样本报告：

1. 必需关节归一化 MAE、P95、PCK 和缺失率；
2. 左右交换、跳点、抖动和跨帧稳定性；
3. 相同人工事件边界下 13 项特征的误差与 unavailable 变化；
4. 端到端吞吐、显存、失败率和长视频稳定性；
5. 预注册门槛冻结后，只开启一次密封保留视频；
6. 全量报告改善项与退化项，不只挑最好结果。

Pose 候选即使通过，也只说明可以考虑更换 F2 测量入口；正式 A～E 仍需要事件真值、特征误差、教练标定、独立测试、可信晋级账本和运行时绑定。

## 9. 许可证边界

MMPose 代码仓库采用 Apache-2.0，不自动证明所有预训练 checkpoint、训练数据来源及其衍生权重都满足产品商业分发要求。X384 当前只作为本地研发影子候选；在产品打包、对外分发或商业部署前，需要单独完成 checkpoint、训练数据、衍生权重和第三方依赖的许可证审查。

## 10. 机器证据

- X 候选注册表：`models/rtmpose/m95-shadow-candidates.json`；
- X 本机烟测：`reports/m95-rtmpose-x-shadow/smoke-report.json`；
- 微调就绪审计：`reports/m95-pose-finetune-readiness/readiness.json`；
- M95 字段与验证记录：`reports/m95-model-candidate-finetune-readiness/field-change-record.json`；
- 可编辑架构图：`docs/diagrams/RallyMate完整系统架构_当前态_v1.0.drawio` 第 5 页。
